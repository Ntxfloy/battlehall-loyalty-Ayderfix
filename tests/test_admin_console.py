from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.admin_auth import hash_password, verify_password
from app.models import AdminUser
from app.services import clubs as clubs_service


def test_password_hash_roundtrip():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed) is True
    assert verify_password("wrong", hashed) is False


def test_password_hash_is_salted():
    a = hash_password("same-password")
    b = hash_password("same-password")
    assert a != b   # разная соль на каждый вызов


@pytest.fixture
def client(db):
    from app.main import app

    return TestClient(app)


def _login(client, username="admin", password="test-password-123"):
    response = client.post("/api/console/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response


def test_login_sets_cookie_and_me_works(client, db):
    _login(client)
    me = client.get("/api/console/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "admin"


def test_wrong_password_rejected(client, db):
    response = client.post("/api/console/auth/login", json={"username": "admin", "password": "nope"})
    assert response.status_code == 401


def test_me_without_login_is_401(client):
    assert client.get("/api/console/auth/me").status_code == 401


def test_logout_clears_session(client, db):
    _login(client)
    assert client.get("/api/console/auth/me").status_code == 200
    client.post("/api/console/auth/logout")
    assert client.get("/api/console/auth/me").status_code == 401


def test_console_endpoints_require_login(client, db):
    assert client.get("/api/console/users").status_code == 401
    assert client.get("/api/console/clubs").status_code == 401
    assert client.get("/api/console/reports").status_code == 401
    assert client.get("/api/console/logs").status_code == 401


def test_service_token_still_works_on_old_admin_routes(client, db, user):
    """/api/admin/* обязан продолжать пускать по X-Admin-Token — им пользуются
    внешние скрипты, которые не умеют логиниться куками."""
    response = client.post(
        "/api/admin/pts/grant",
        headers={"X-Admin-Token": "change-me-too"},
        json={"telegram_id": user.telegram_id, "amount": 100, "comment": "тест"},
    )
    assert response.status_code == 200
    assert response.json()["balance"] == 100


def test_session_cookie_also_authorizes_old_admin_routes(client, db, user):
    _login(client)
    response = client.post(
        "/api/admin/pts/grant",
        json={"telegram_id": user.telegram_id, "amount": 50, "comment": "тест"},
    )
    assert response.status_code == 200


# --- клубы ---

def test_create_and_list_clubs(client, db, club):
    _login(client)
    response = client.post("/api/console/clubs", json={"slug": "north", "name": "Северный"})
    assert response.status_code == 200
    token = response.json()["webhook_token"]
    assert len(token) == 40

    listed = client.get("/api/console/clubs").json()["items"]
    slugs = {c["slug"] for c in listed}
    assert {"main", "north"} <= slugs


def test_duplicate_club_slug_rejected(client, db, club):
    _login(client)
    response = client.post("/api/console/clubs", json={"slug": "main", "name": "Дубликат"})
    assert response.status_code == 400


def test_rotate_token_changes_it(client, db, club):
    _login(client)
    before = client.get("/api/console/clubs").json()["items"][0]["webhook_token"]
    rotated = client.post(f"/api/console/clubs/{club.id}/rotate-token")
    assert rotated.status_code == 200
    assert rotated.json()["webhook_token"] != before


def test_deactivate_club_blocks_its_webhook(client, db, club):
    _login(client)
    client.patch(f"/api/console/clubs/{club.id}", json={"is_active": False})

    response = client.post(
        f"/api/webhooks/oasys/{club.slug}/session-start",
        headers={"X-OASys-Token": club.oasys_webhook_token},
        json={"session_id": "x-1", "pc_number": 1, "started_at": datetime.now(timezone.utc).isoformat()},
    )
    assert response.status_code == 404


# --- тестовые запросы из панели ---

def test_panel_test_request_creates_user_on_the_fly(client, db, club):
    """В отличие от настоящего вебхука OASys, тестовый инструмент в панели
    не должен требовать, чтобы гость уже был зарегистрирован."""
    _login(client)
    response = client.post(
        "/api/console/test/session-start",
        json={"club_slug": club.slug, "telegram_id": 909090, "pc_number": 5},
    )
    assert response.status_code == 200
    assert response.json()["created"] is True


def test_panel_can_simulate_full_session(client, db, club, user):
    _login(client)
    started = client.post(
        "/api/console/test/session-start",
        json={"club_slug": club.slug, "telegram_id": user.telegram_id, "pc_number": 12},
    )
    assert started.status_code == 200
    session_id = started.json()["session_id"]

    ended = client.post(
        "/api/console/test/session-end",
        json={
            "club_slug": club.slug,
            "telegram_id": user.telegram_id,
            "session_id": session_id,
            "duration_minutes": 90,
        },
    )
    assert ended.status_code == 200
    assert ended.json()["minutes"] == 90


# --- пользователи ---

def test_user_search_by_telegram_id(client, db, user):
    _login(client)
    response = client.get(f"/api/console/users?q={user.telegram_id}")
    assert response.status_code == 200
    ids = {item["telegram_id"] for item in response.json()["items"]}
    assert user.telegram_id in ids


def test_user_detail_includes_history(client, db, club, user):
    _login(client)
    client.post(
        "/api/console/test/session-start",
        json={"club_slug": club.slug, "telegram_id": user.telegram_id, "pc_number": 20},
    )
    detail = client.get(f"/api/console/users/{user.telegram_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["user"]["telegram_id"] == user.telegram_id
    assert len(body["visits"]) == 1
    assert "weekly" in body["achievements"]


def test_unknown_user_detail_404(client, db):
    _login(client)
    assert client.get("/api/console/users/999999999").status_code == 404


# --- журнал действий ---

def test_actions_are_logged(client, db, user):
    _login(client)
    client.post(
        "/api/admin/pts/grant",
        json={"telegram_id": user.telegram_id, "amount": 10, "comment": "лог-тест"},
    )
    logs = client.get("/api/console/logs").json()["items"]
    actions = {entry["action"] for entry in logs}
    assert "login" in actions
    assert "pts_grant" in actions


# --- отчёты по клубам ---

def test_reports_aggregate_by_club(client, db, club, user):
    _login(client)
    clubs_service.create(db, "south", "Южный")

    client.post(
        "/api/console/test/session-start",
        json={"club_slug": "main", "telegram_id": user.telegram_id, "pc_number": 20},
    )
    client.post(
        "/api/console/test/session-start",
        json={"club_slug": "south", "telegram_id": user.telegram_id, "pc_number": 20, "session_id": "south-1"},
    )

    report = client.get("/api/console/reports").json()
    by_slug = {c["club"]["slug"]: c for c in report["clubs"]}
    assert by_slug["main"]["sessions"] == 1
    assert by_slug["south"]["sessions"] == 1


def test_same_session_id_allowed_in_different_clubs(db, club, user):
    """Регрессия: разные клубы сети могут переиспользовать одинаковые
    session_id из своих независимых инсталляций OASys."""
    from app.schemas import SessionStartPayload
    from app.services import clubs as clubs_svc, sessions

    other = clubs_svc.create(db, "east", "Восточный")
    payload = SessionStartPayload(
        session_id="dup-1",
        pc_number=20,
        started_at=datetime.now(timezone.utc),
        telegram_id=user.telegram_id,
    )
    _, created_a = sessions.start_session(db, club, payload)
    _, created_b = sessions.start_session(db, other, payload)
    assert created_a is True
    assert created_b is True
