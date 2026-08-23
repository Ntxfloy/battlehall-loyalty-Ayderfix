"""Логин администратора: PBKDF2-пароль + подписанная кука сессии.

Отдельный от Telegram-авторизации гостя контур — сюда заходят люди с клавиатурой,
поэтому обычная форма логин/пароль, без внешних зависимостей (bcrypt и т.п.),
чтобы не раздувать requirements.txt ради внутреннего инструмента.
"""

import base64
import hashlib
import hmac
import secrets
import time
from typing import NamedTuple

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app import permissions as perms
from app.config import get_settings, is_placeholder_secret, is_production, secrets_match
from app.db import get_db
from app.models import AdminUser

settings = get_settings()

SESSION_COOKIE = "bh_admin_session"
_PBKDF2_ITERATIONS = 310_000


class Caller(NamedTuple):
    """Кто выполняет действие. label уходит в журнал."""

    label: str
    admin: AdminUser | None
    is_service: bool


# --- пароли ---


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest_hex = stored.split("$", 1)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return hmac.compare_digest(digest.hex(), digest_hex)


# --- подписанная кука сессии: "<admin_id>:<expires_ts>:<hmac>" в base64 ---

def _sign(payload: str) -> str:
    return hmac.new(settings.admin_session_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def create_session_value(admin_id: int) -> str:
    expires = int(time.time()) + settings.admin_session_ttl_hours * 3600
    payload = f"{admin_id}:{expires}"
    token = f"{payload}:{_sign(payload)}"
    return base64.urlsafe_b64encode(token.encode()).decode()


def read_session_value(value: str) -> int | None:
    try:
        decoded = base64.urlsafe_b64decode(value.encode()).decode()
        admin_id_str, expires_str, signature = decoded.split(":", 2)
    except (ValueError, UnicodeDecodeError):
        return None

    payload = f"{admin_id_str}:{expires_str}"
    if not hmac.compare_digest(_sign(payload), signature):
        return None
    if int(expires_str) < time.time():
        return None
    return int(admin_id_str)


# --- FastAPI-зависимости ---

def current_admin_user(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    """Строгая проверка для страниц/API самой панели: только валидная кука."""
    raw = request.cookies.get(SESSION_COOKIE)
    admin_id = read_session_value(raw) if raw else None
    if admin_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Нужен вход в админку")

    admin = db.get(AdminUser, admin_id)
    if admin is None or not admin.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Учётка отключена")
    return admin


def _service_token_valid(provided: str | None) -> bool:
    return secrets_match(provided, settings.admin_token)



def forbid_in_production() -> None:
    """404, а не 403: наличие тестовых ручек в проде не подтверждаем."""
    if is_production():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


def require_permission(permission: str):
    """Фабрика зависимости: пускает владельца всегда, сотрудника — только
    если владелец выдал ему это право."""

    def dependency(admin: AdminUser = Depends(current_admin_user)) -> AdminUser:
        if not perms.has(admin, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Нет доступа: {perms.LABELS.get(permission, permission)}",
            )
        return admin

    return dependency


def require_admin_permission(permission: str, *, allow_service_token: bool = True):
    """Для служебных ручек /api/admin/*.

    allow_service_token=False обязателен для всего, что двигает деньги или критичные права:
    один статический токен на всех не даёт атрибуции в журнале.
    """

    def dependency(
        request: Request,
        x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
        db: Session = Depends(get_db),
    ) -> Caller:
        if x_admin_token is not None and x_admin_token.strip():
            if not allow_service_token:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Эта операция выполняется только под учётной записью администратора",
                )
            if not _service_token_valid(x_admin_token):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Неверный или небезопасный сервисный токен",
                )
            return Caller("service-token", None, True)

        raw = request.cookies.get(SESSION_COOKIE)
        admin_id = read_session_value(raw) if raw else None
        if admin_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Нужен вход в админку",
            )

        admin = db.get(AdminUser, admin_id)
        if admin is None or not admin.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Учётка отключена или не существует",
            )

        if not perms.has(admin, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Нет доступа: {perms.LABELS.get(permission, permission)}",
            )

        return Caller(admin.username, admin, False)

    return dependency
