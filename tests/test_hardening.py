"""Регрессии пакета 1.3: health, CORS-среда, потолок сессии, демо-редирект."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.main import safe_redirect
from app.schemas import MAX_SESSION_MINUTES, AdminLoginRequest, SessionEndPayload, TestSessionEndRequest


def test_health_does_not_leak_env(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"status": "ok"}
    assert "env" not in body
    assert "app_env" not in body


def test_safe_redirect_rejects_external_urls():
    assert safe_redirect("https://evil.test/phish") == "/"
    assert safe_redirect("//evil.test") == "/"
    assert safe_redirect("/admin") == "/admin"
    assert safe_redirect("/admin?x=1") == "/admin?x=1"
    assert safe_redirect(None) == "/"


def test_session_end_rejects_huge_duration():
    with pytest.raises(ValidationError):
        SessionEndPayload(
            session_id="too-long",
            pc_number=15,
            started_at=datetime.now(timezone.utc),
            telegram_id=1,
            duration_minutes=100_000,
        )


def test_test_session_end_rejects_huge_duration():
    with pytest.raises(ValidationError):
        TestSessionEndRequest(
            club_slug="main",
            telegram_id=1,
            session_id="too-long",
            duration_minutes=MAX_SESSION_MINUTES + 1,
        )


def test_login_password_is_capped():
    with pytest.raises(ValidationError):
        AdminLoginRequest(username="admin", password="x" * 10_000)
