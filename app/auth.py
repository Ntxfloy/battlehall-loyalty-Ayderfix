"""Аутентификация Mini App по Telegram initData.

Фронт при каждом запросе шлёт заголовок `X-Telegram-Init-Data` — это подписанная
Telegram строка. Проверяем подпись ключом бота: без неё запросы принимать нельзя,
иначе кто угодно подставит чужой telegram_id и заберёт чужие PTS.
"""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User
from app.services import referrals

settings = get_settings()

# Сколько живёт подпись initData. Телеграм не ограничивает, ограничиваем сами.
INIT_DATA_MAX_AGE_SECONDS = 24 * 60 * 60


class AuthError(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def parse_init_data(init_data: str, bot_token: str) -> dict:
    """Проверяет подпись и возвращает разобранные поля initData."""
    if not init_data:
        raise AuthError("Пустой initData")
    if not bot_token:
        raise AuthError("BOT_TOKEN не настроен на сервере")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise AuthError("В initData нет подписи")

    data_check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calculated, received_hash):
        raise AuthError("Подпись initData не сходится")

    auth_date = int(pairs.get("auth_date", "0"))
    if auth_date and time.time() - auth_date > INIT_DATA_MAX_AGE_SECONDS:
        raise AuthError("initData устарел, перезапусти приложение")

    user_raw = pairs.get("user")
    if not user_raw:
        raise AuthError("В initData нет пользователя")

    pairs["user"] = json.loads(user_raw)
    return pairs


def get_or_create_user(db: Session, tg_user: dict, start_param: str | None = None) -> User:
    telegram_id = int(tg_user["id"])
    user = db.query(User).filter(User.telegram_id == telegram_id).one_or_none()

    if user is None:
        user = User(
            telegram_id=telegram_id,
            username=tg_user.get("username"),
            first_name=tg_user.get("first_name"),
            referral_code=referrals.generate_code(db),
        )
        db.add(user)
        db.flush()
    else:
        # держим профиль свежим — ник в Telegram меняется
        changed = False
        if tg_user.get("username") != user.username:
            user.username = tg_user.get("username")
            changed = True
        if tg_user.get("first_name") != user.first_name:
            user.first_name = tg_user.get("first_name")
            changed = True
        if changed:
            db.add(user)

    if start_param:
        referrals.attach(db, user, start_param)

    db.commit()
    return user


def current_user(
    x_telegram_init_data: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not x_telegram_init_data:
        if settings.dev_allow_fake_auth:
            return get_or_create_user(
                db,
                {
                    "id": settings.dev_fake_telegram_id,
                    "username": "dev_user",
                    "first_name": "Разработчик",
                },
            )
        raise AuthError("Нет заголовка X-Telegram-Init-Data")

    data = parse_init_data(x_telegram_init_data, settings.bot_token)
    return get_or_create_user(db, data["user"], data.get("start_param"))
