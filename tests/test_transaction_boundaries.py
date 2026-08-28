"""Тесты единых транзакционных границ: сервисы не коммитят сами, сбой аудита откатывает операцию."""

import sys
import pytest
from sqlalchemy import select

from app.admin_auth import create_session_value
from app.config import get_settings
from app.db import SessionLocal
from app.models import AdminRole, AdminUser, Club, RedemptionStatus, Reward, Wheel
from app.services import audit, pts, rewards, sheets
import seed


def test_use_code_does_not_commit(db, user):
    """Сервис use_code не коммитит: rollback откатывает переход статуса."""
    pts.credit(db, user, 5000, comment="тест")
    db.commit()

    reward = db.query(Reward).filter(Reward.code == "cash_300").one()
    row = rewards.redeem(db, user, reward.id)
    db.commit()

    code = row.code
    rewards.use_code(db, code, "desk1")

    # Откатываем незакоммиченную транзакцию
    db.rollback()
    db.refresh(row)
    assert row.status == RedemptionStatus.PENDING
    assert row.used_by is None
    assert row.used_at is None


def test_approve_code_does_not_commit(db, user):
    """Сервис approve_code не коммитит: rollback откатывает статус обратно в SUBMITTED."""
    pts.credit(db, user, 5000, comment="тест")
    db.commit()

    reward = db.query(Reward).filter(Reward.code == "cash_300").one()
    row = rewards.redeem(db, user, reward.id)
    db.commit()

    rewards.use_code(db, row.code, "desk1")
    db.commit()
    db.refresh(row)
    assert row.status == RedemptionStatus.SUBMITTED

    rewards.approve_code(db, row.code, "owner1")
    db.rollback()

    db.refresh(row)
    assert row.status == RedemptionStatus.SUBMITTED
    assert row.approved_by is None
    assert row.approved_at is None


def test_audit_failure_rolls_back_code_submit(client, db, user, monkeypatch):
    """Падение записи в журнал аудита не оставляет погашенный код в базе."""
    desk_admin = AdminUser(
        username="desk_staff",
        password_hash="fakehash",
        display_name="Сотрудник стойки",
        role=AdminRole.STAFF,
        permissions='["codes.submit", "codes.view"]',
    )
    db.add(desk_admin)
    pts.credit(db, user, 5000, comment="тест")
    db.commit()

    reward = db.query(Reward).filter(Reward.code == "cash_300").one()
    row = rewards.redeem(db, user, reward.id)
    db.commit()
    code = row.code

    token = create_session_value(desk_admin.id)
    client.cookies.set("bh_admin_session", token)

    def broken_audit(*args, **kwargs):
        raise RuntimeError("Audit database failure")

    monkeypatch.setattr(audit, "log", broken_audit)

    with pytest.raises(RuntimeError, match="Audit database failure"):
        client.post("/api/console/desk/submit", json={"code": code})

    db.rollback()
    db.refresh(row)
    assert row.status == RedemptionStatus.PENDING
    assert row.used_by is None

    client.cookies.clear()
    monkeypatch.undo()


def test_admin_create_rolls_back_without_audit(client, db, monkeypatch):
    """Сбой записи в журнал аудита откатывает создание учётной записи администратора."""
    owner = AdminUser(
        username="owner_boss",
        password_hash="fakehash",
        display_name="Владелец",
        role=AdminRole.OWNER,
    )
    db.add(owner)
    db.commit()

    token = create_session_value(owner.id)
    client.cookies.set("bh_admin_session", token)

    def broken_audit(*args, **kwargs):
        raise RuntimeError("Audit storage crashed")

    monkeypatch.setattr(audit, "log", broken_audit)

    with pytest.raises(RuntimeError, match="Audit storage crashed"):
        client.post(
            "/api/console/admins",
            json={
                "username": "new_staff_member",
                "password": "password123",
                "display_name": "Новый сотрудник",
                "permissions": ["codes.view"],
            },
        )

    db.rollback()
    created = db.execute(select(AdminUser).where(AdminUser.username == "new_staff_member")).scalar_one_or_none()
    assert created is None

    client.cookies.clear()
    monkeypatch.undo()


def test_approve_survives_sheets_failure(client, db, user, monkeypatch, settings_patch):
    """Падение Google Sheets не откатывает подтверждение кода."""
    owner = AdminUser(
        username="owner_approver",
        password_hash="fakehash",
        display_name="Владелец",
        role=AdminRole.OWNER,
    )
    db.add(owner)
    pts.credit(db, user, 5000, comment="тест")
    db.commit()

    reward = db.query(Reward).filter(Reward.code == "cash_300").one()
    row = rewards.redeem(db, user, reward.id)
    db.commit()

    rewards.use_code(db, row.code, "desk1")
    db.commit()

    token = create_session_value(owner.id)
    client.cookies.set("bh_admin_session", token)

    settings_patch(google_autoexport=True)

    def broken_export_one(db_sess, redemption):
        raise sheets.SheetsError("Google API 503 Service Unavailable")

    monkeypatch.setattr(sheets, "export_one", broken_export_one)

    resp = client.post("/api/console/desk/approve", json={"code": row.code})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["status"] == RedemptionStatus.APPROVED
    assert resp.json()["exported_to_sheets"] is False

    db.refresh(row)
    assert row.status == RedemptionStatus.APPROVED
    assert row.approved_by == owner.username
    assert row.exported_at is None

    client.cookies.clear()


def test_seed_main_commits(db, monkeypatch):
    """seed.main() коммитит сам: после его завершения строки видны новой сессии."""
    db.query(AdminUser).delete()
    db.query(Club).delete()
    db.query(Wheel).delete()
    db.commit()

    monkeypatch.setattr(sys, "argv", ["seed.py"])
    seed.main()                      # никаких commit() из теста

    with SessionLocal() as verify:
        assert verify.execute(select(Club).where(Club.slug == "main")).scalar_one_or_none() is not None
        saved_admin = verify.execute(
            select(AdminUser).where(AdminUser.username == get_settings().admin_default_username)
        ).scalar_one_or_none()
        assert saved_admin is not None
        assert saved_admin.role == AdminRole.OWNER
