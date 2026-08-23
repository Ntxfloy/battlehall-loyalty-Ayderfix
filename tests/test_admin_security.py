"""Регрессии безопасности входа в админку.

Закрывает три дыры из роадмапа:
1. перебор пароля ничем не ограничен;
2. логин чувствителен к регистру, хотя учётки хранятся в нижнем;
3. старые куки живут после смены пароля.
"""

from app.admin_auth import create_session_value
from app.rate_limit import MAX_LOGIN_FAILURES
from app.services import admins as admins_service

PASSWORD = "test-password-123"


def _fresh_client():
    """Отдельный браузер со своим набором кук."""
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _login(client, username="admin", password=PASSWORD):
    return client.post("/api/console/auth/login", json={"username": username, "password": password})


# --- регистр и пробелы в логине ---

def test_login_is_case_insensitive(client, db):
    assert _login(client, username="AdMiN").status_code == 200
    assert client.get("/api/console/auth/me").json()["username"] == "admin"


def test_login_ignores_edge_whitespace(client, db):
    assert _login(client, username="  admin ").status_code == 200


# --- лимит попыток ---

def test_brute_force_is_rate_limited(client, db):
    for _ in range(MAX_LOGIN_FAILURES):
        assert _login(client, password="wrong").status_code == 401

    blocked = _login(client, password="wrong")
    assert blocked.status_code == 429
    assert blocked.headers.get("Retry-After")

    # Даже верный пароль не проходит, пока действует блокировка.
    assert _login(client).status_code == 429


def test_successful_login_resets_failure_counter(client, db):
    for _ in range(MAX_LOGIN_FAILURES - 1):
        assert _login(client, password="wrong").status_code == 401
    assert _login(client).status_code == 200

    # Счётчик обнулён: снова есть полный запас попыток.
    for _ in range(MAX_LOGIN_FAILURES - 1):
        assert _login(client, password="wrong").status_code == 401
    assert _login(client).status_code == 200


def test_failed_login_is_written_to_audit(client, db):
    _login(client, password="wrong")
    _login(client)

    actions = {row["action"] for row in client.get("/api/console/logs").json()["items"]}
    assert "login_failed" in actions


# --- отзыв сессий при смене пароля ---

def test_password_change_revokes_other_sessions(client, db):
    other = _fresh_client()
    assert _login(other).status_code == 200
    assert _login(client).status_code == 200

    changed = client.post(
        "/api/console/auth/password",
        json={"current_password": PASSWORD, "new_password": "brand-new-password-9"},
    )
    assert changed.status_code == 200, changed.text

    # Чужая сессия со старым паролем больше не действует.
    assert other.get("/api/console/auth/me").status_code == 401
    # А тот, кто менял пароль, остаётся в панели: кука перевыпущена.
    assert client.get("/api/console/auth/me").status_code == 200


def test_owner_reset_revokes_staff_session(client, db):
    staff = admins_service.create(db, "victim", "password123")
    staff_id = staff.id
    staff_hash = staff.password_hash
    db.commit()

    staff_client = _fresh_client()
    staff_client.cookies.set("bh_admin_session", create_session_value(staff_id, staff_hash))
    assert staff_client.get("/api/console/auth/me").status_code == 200

    _login(client)
    reset = client.post(
        f"/api/console/admins/{staff_id}/password",
        json={"password": "another-password-1"},
    )
    assert reset.status_code == 200, reset.text

    assert staff_client.get("/api/console/auth/me").status_code == 401


def test_cookie_without_password_fingerprint_is_rejected(client, db):
    """Кука старого образца (без отпечатка пароля) больше не пускает."""
    client.cookies.set("bh_admin_session", create_session_value(1))
    assert client.get("/api/console/auth/me").status_code == 401


def test_tampered_cookie_is_rejected(client, db):
    _login(client)
    original = client.cookies.get("bh_admin_session")
    client.cookies.set("bh_admin_session", original[:-4] + "AAAA")
    assert client.get("/api/console/auth/me").status_code == 401
