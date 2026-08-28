"""Тесты защиты вебхуков OASys и сервисных токенов."""

import pytest
from fastapi import HTTPException
from app.api.webhooks import _resolve_club


def test_webhook_with_valid_token_accepted(client, db, club, user):
    """Вебхук с правильным длинным токеном принимается (200)."""
    valid_token = "valid_secure_webhook_token_32_chars_long"
    club.oasys_webhook_token = valid_token
    db.add(club)
    db.commit()

    resp = client.post(
        f"/api/webhooks/oasys/{club.slug}/session-start",
        headers={"X-OASys-Token": valid_token},
        json={
            "session_id": "test-webhook-1",
            "pc_number": 5,
            "started_at": "2026-08-23T00:00:00Z",
            "telegram_id": user.telegram_id,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "created"



def test_webhook_with_placeholder_club_token_rejected(client, db, club):
    """Если у клуба токен-заглушка ('change-me'), вызов отклоняется с 401."""
    club.oasys_webhook_token = "change-me"
    db.add(club)
    db.commit()

    resp = client.post(
        f"/api/webhooks/oasys/{club.slug}/session-start",
        headers={"X-OASys-Token": "change-me"},
        json={
            "session_id": "test-webhook-2",
            "pc_number": 5,
            "started_at": "2026-08-23T00:00:00Z",
            "telegram_id": 999002,
        },
    )
    assert resp.status_code == 401


def test_webhook_with_invalid_token_header_rejected(client, db, club):
    """Неверный или отсутствующий токен в заголовке возвращает 401 Unauthorized."""
    valid_token = "valid_secure_webhook_token_32_chars_long"
    club.oasys_webhook_token = valid_token
    db.add(club)
    db.commit()

    # Без заголовка
    resp_no_header = client.post(
        f"/api/webhooks/oasys/{club.slug}/session-start",
        json={"session_id": "test-3", "pc_number": 5, "started_at": "2026-08-23T00:00:00Z", "telegram_id": 999003},
    )
    assert resp_no_header.status_code == 401

    # Неверный заголовок
    resp_wrong_header = client.post(
        f"/api/webhooks/oasys/{club.slug}/session-start",
        headers={"X-OASys-Token": "wrong_token_secret_1234567890123456"},
        json={"session_id": "test-4", "pc_number": 5, "started_at": "2026-08-23T00:00:00Z", "telegram_id": 999004},
    )
    assert resp_wrong_header.status_code == 401


def test_webhook_long_non_latin1_token_returns_401(db, club):
    """Длинный токен из кириллицы/non-latin1 возвращает 401 без 500 исключения."""
    club.oasys_webhook_token = "a" * 40
    db.add(club)
    db.commit()

    with pytest.raises(HTTPException) as exc:
        _resolve_club(db, club.slug, "Ж" * 40)

    assert exc.value.status_code == 401


def test_webhook_long_latin1_token_returns_401(db, club):
    """Длинный Latin-1 несовпадающий токен возвращает 401."""
    club.oasys_webhook_token = "a" * 40
    db.add(club)
    db.commit()

    with pytest.raises(HTTPException) as exc:
        _resolve_club(db, club.slug, "é" * 40)

    assert exc.value.status_code == 401


def test_placeholder_admin_service_token_returns_401_not_500(client, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "admin_token", "change-me")

    response = client.get(
        "/api/admin/redemptions/NONEXISTENT",
        headers={"X-Admin-Token": "change-me"},
    )
    assert response.status_code == 401


def test_valid_admin_service_token_still_works(client, monkeypatch):
    from app.config import get_settings
    valid_token = "a" * 40
    monkeypatch.setattr(get_settings(), "admin_token", valid_token)

    response = client.get(
        "/api/admin/redemptions/NONEXISTENT",
        headers={"X-Admin-Token": valid_token},
    )
    assert response.status_code == 404   # 404 = прошел auth, код не найден


def test_list_clubs_returns_webhook_configured_boolean_not_token(client, db, club):
    from app.admin_auth import create_session_value
    client.cookies.set("bh_admin_session", create_session_value(1))

    resp = client.get("/api/console/clubs")
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert "webhook_token" not in item
    assert "webhook_configured" in item
    assert isinstance(item["webhook_configured"], bool)
