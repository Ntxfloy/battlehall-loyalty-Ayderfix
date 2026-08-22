"""Тесты выгрузки в Google Sheets.

В сеть не ходим: подменяем лист заглушкой и проверяем то, что реально
может сломаться — состав строки, отметку о выгрузке и поведение при сбое.
"""

import pytest
from fastapi.testclient import TestClient

from app.models import RedemptionStatus, Reward
from app.services import pts, rewards, sheets


class FakeWorksheet:
    """Заглушка листа: копит записанные строки, умеет притворяться упавшей."""

    def __init__(self, fail: bool = False):
        self.rows: list[list] = []
        self.fail = fail
        self.title = "Компенсации"
        self.row_count = 1000

    def append_rows(self, rows, value_input_option=None):
        if self.fail:
            raise RuntimeError("Google недоступен")
        self.rows.extend(rows)


@pytest.fixture
def client(db):
    from app.main import app

    return TestClient(app)


def _login(client, username="admin", password="test-password-123"):
    response = client.post("/api/console/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text


def _approved_code(client, db, user) -> str:
    """Проводит код весь путь: выдан -> внесён -> подтверждён."""
    pts.credit(db, user, 5000, comment="тест")
    db.commit()
    reward = db.query(Reward).filter(Reward.code == "cash_300").one()
    code = rewards.redeem(db, user, reward.id).code

    _login(client)
    client.post("/api/console/desk/submit", json={"code": code})
    client.post("/api/console/desk/approve", json={"code": code})
    return code


# --- конфигурация ---

def test_not_configured_by_default(db):
    """Без настроек интеграция молчит, а не роняет приложение."""
    assert sheets.is_configured() is False


def test_empty_queue_does_not_touch_google(db):
    """Пустая очередь не должна дёргать сеть — и не должна быть ошибкой."""
    assert sheets.export_pending(db)["exported"] == 0


def test_export_without_config_raises_clear_error(client, db, user):
    _approved_code(client, db, user)
    with pytest.raises(sheets.SheetsError, match="не настроен"):
        sheets.export_pending(db)


def test_status_endpoint_reports_pending_count(client, db, user):
    _approved_code(client, db, user)
    status = client.get("/api/console/sheets/status").json()

    assert status["configured"] is False
    assert status["pending"] == 1


# --- выгрузка ---

def test_export_writes_row_and_marks_it(client, db, user, monkeypatch):
    code = _approved_code(client, db, user)
    fake = FakeWorksheet()
    monkeypatch.setattr(sheets, "_worksheet", lambda: fake)

    result = sheets.export_pending(db)

    assert result["exported"] == 1
    assert len(fake.rows) == 1
    assert fake.rows[0][0] == code                     # код в первой колонке
    assert "300 ₽ на игровой счёт" in fake.rows[0]     # награда попала в строку


def test_exported_row_is_not_sent_twice(client, db, user, monkeypatch):
    _approved_code(client, db, user)
    fake = FakeWorksheet()
    monkeypatch.setattr(sheets, "_worksheet", lambda: fake)

    sheets.export_pending(db)
    second = sheets.export_pending(db)

    assert second["exported"] == 0
    assert len(fake.rows) == 1


def test_failed_export_leaves_row_in_queue(client, db, user, monkeypatch):
    """Если Google упал, отметку о выгрузке ставить нельзя — иначе строка
    потеряется и в таблицу никогда не попадёт."""
    _approved_code(client, db, user)
    monkeypatch.setattr(sheets, "_worksheet", lambda: FakeWorksheet(fail=True))

    with pytest.raises(sheets.SheetsError):
        sheets.export_pending(db)

    assert len(sheets.pending_export(db)) == 1


def test_only_approved_rows_are_exported(client, db, user, monkeypatch):
    """Внесённый, но не подтверждённый код в таблицу уходить не должен."""
    pts.credit(db, user, 5000, comment="тест")
    db.commit()
    reward = db.query(Reward).filter(Reward.code == "cash_300").one()
    code = rewards.redeem(db, user, reward.id).code

    _login(client)
    client.post("/api/console/desk/submit", json={"code": code})   # без approve

    assert sheets.pending_export(db) == []


def test_autoexport_on_approve(client, db, user, monkeypatch):
    """Подтверждение сразу отправляет строку, если интеграция настроена."""
    fake = FakeWorksheet()
    monkeypatch.setattr(sheets, "_worksheet", lambda: fake)
    monkeypatch.setattr(sheets, "is_configured", lambda: True)

    code = _approved_code(client, db, user)

    assert len(fake.rows) == 1
    assert fake.rows[0][0] == code
    assert sheets.pending_export(db) == []


def test_approve_survives_broken_sheets(client, db, user, monkeypatch):
    """Недоступный Google не должен ломать подтверждение кода."""
    monkeypatch.setattr(sheets, "_worksheet", lambda: FakeWorksheet(fail=True))
    monkeypatch.setattr(sheets, "is_configured", lambda: True)

    code = _approved_code(client, db, user)

    row = rewards.lookup(db, code)
    assert row.status == RedemptionStatus.APPROVED     # подтверждение прошло
    assert row.exported_at is None                     # но осталось в очереди


def test_row_contains_guest_contacts(client, db, user, monkeypatch):
    """В таблице должно быть по чему найти гостя: ник и телефон."""
    user.username = "guest_nick"
    user.phone = "79995554433"
    db.add(user)
    db.commit()

    _approved_code(client, db, user)
    fake = FakeWorksheet()
    monkeypatch.setattr(sheets, "_worksheet", lambda: fake)
    sheets.export_pending(db)

    row = fake.rows[0]
    assert "@guest_nick" in row
    assert "79995554433" in row


def test_export_endpoint_requires_approve_permission(client, db):
    from app import permissions as perms
    from app.services import admins as admins_service

    admins_service.create(db, "deskonly", "password123", permissions=[perms.CODES_VIEW])
    _login(client, "deskonly", "password123")

    assert client.post("/api/console/sheets/export").status_code == 403
