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

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import AdminUser

settings = get_settings()

SESSION_COOKIE = "bh_admin_session"
_PBKDF2_ITERATIONS = 310_000


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


def require_permission(permission: str):
    """Фабрика зависимости: пускает владельца всегда, сотрудника — только
    если владелец выдал ему это право."""

    def dependency(admin: AdminUser = Depends(current_admin_user)) -> AdminUser:
        from app import permissions as perms

        if not perms.has(admin, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Нет доступа: {perms.LABELS.get(permission, permission)}",
            )
        return admin

    return dependency


def require_admin(
    request: Request,
    x_admin_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> str:
    """Мягкая проверка для существующих служебных ручек /api/admin/*:
    пускает либо по сервисному токену (скрипты), либо по куке панели (браузер).
    Возвращает идентификатор вызывающего — для журнала действий."""
    if x_admin_token and hmac.compare_digest(x_admin_token, settings.admin_token):
        return "service-token"

    raw = request.cookies.get(SESSION_COOKIE)
    admin_id = read_session_value(raw) if raw else None
    if admin_id is not None:
        admin = db.get(AdminUser, admin_id)
        if admin is not None and admin.is_active:
            return admin.username

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нужен токен или вход в админку")
