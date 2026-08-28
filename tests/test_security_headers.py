"""Защитные заголовки легко потерять при рефакторинге middleware,
и пропажу никто не заметит глазами. Проверяем их явно."""

from app.main import (
    API_CONTENT_SECURITY_POLICY,
    CONTENT_SECURITY_POLICY,
    PERMISSIONS_POLICY,
)


def test_html_response_has_csp(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers["Content-Security-Policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert response.headers["Permissions-Policy"] == PERMISSIONS_POLICY


def test_api_gets_strict_csp(client):
    # Ответ может быть любым (включая 401) — заголовки вешаются в любом случае.
    response = client.get("/api/me")
    assert response.headers["Content-Security-Policy"] == API_CONTENT_SECURITY_POLICY
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_script_sources_are_limited(client):
    csp = client.get("/health").headers["Content-Security-Policy"]
    assert "script-src 'self' https://telegram.org" in csp
    assert "object-src 'none'" in csp
    assert "'unsafe-eval'" not in csp
    # Скрипты без inline, стили — с inline осознанно (style="…" в разметке).
    assert "script-src 'self' https://telegram.org;" in csp + ";"
    assert "style-src 'self' 'unsafe-inline'" in csp


def test_hsts_absent_in_local_env(client):
    # APP_ENV=test — локальная среда, HSTS тут только мешал бы.
    assert "Strict-Transport-Security" not in client.get("/health").headers
