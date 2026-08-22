"""Приём событий из OASys.

Путь содержит slug клуба: `/api/webhooks/oasys/{club_slug}/session-start`.
Так у каждого клуба сети свой токен и своя идемпотентность `session_id`,
не завязанные на конфиг сервера. Токен — в заголовке `X-OASys-Token`,
сверяется с тем, что хранится в таблице clubs.
"""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Club
from app.schemas import LinkPhonePayload, SessionEndPayload, SessionStartPayload
from app.services import clubs, sessions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks/oasys", tags=["webhooks"])


def _resolve_club(db: Session, slug: str, token: str | None) -> Club:
    club = clubs.get_by_slug(db, slug)
    if club is None or not club.is_active:
        raise HTTPException(status_code=404, detail="Клуб не найден")

    import hmac

    if not token or not hmac.compare_digest(token, club.oasys_webhook_token):
        raise HTTPException(status_code=403, detail="Неверный токен вебхука")
    return club


@router.post("/{club_slug}/session-start")
def session_start(
    club_slug: str,
    payload: SessionStartPayload,
    x_oasys_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    club = _resolve_club(db, club_slug, x_oasys_token)
    try:
        row, created = sessions.start_session(db, club, payload)
    except sessions.UserNotLinked:
        # Не ошибка интеграции: гость просто ещё не в программе лояльности.
        # Отвечаем 200, чтобы OASys не ретраил вечно.
        logger.info("club %s, session %s: гость не привязан, пропускаем", club_slug, payload.session_id)
        return {"status": "skipped", "reason": "user_not_linked"}
    except sessions.SessionIngestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "status": "created" if created else "duplicate",
        "session_id": row.oasys_session_id,
        "zone": row.zone_code,
        "game_day": row.game_day,
    }


@router.post("/{club_slug}/session-end")
def session_end(
    club_slug: str,
    payload: SessionEndPayload,
    x_oasys_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    club = _resolve_club(db, club_slug, x_oasys_token)
    try:
        row, closed = sessions.end_session(db, club, payload)
    except sessions.UserNotLinked:
        logger.info("club %s, session %s: гость не привязан, пропускаем", club_slug, payload.session_id)
        return {"status": "skipped", "reason": "user_not_linked"}
    except sessions.SessionIngestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "status": "closed" if closed else "already_closed",
        "session_id": row.oasys_session_id,
        "minutes": row.duration_minutes,
    }


@router.post("/{club_slug}/link-phone")
def link_phone(
    club_slug: str,
    payload: LinkPhonePayload,
    x_oasys_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """Бот OASys уже знает связку Telegram <-> телефон. Забираем её себе,
    чтобы матчить вебхуки сессий на пользователя программы. Привязка телефона
    сама по себе не клубная (аккаунт гостя один на всю сеть) — slug в пути
    нужен только для проверки токена того клуба, откуда пришёл вебхук."""
    _resolve_club(db, club_slug, x_oasys_token)

    from app.models import User
    from app.services import referrals

    phone = sessions.normalize_phone(payload.phone)
    if not phone:
        raise HTTPException(status_code=400, detail="Некорректный телефон")

    user = db.query(User).filter(User.telegram_id == payload.telegram_id).one_or_none()
    if user is None:
        user = User(
            telegram_id=payload.telegram_id,
            referral_code=referrals.generate_code(db),
        )
        db.add(user)

    user.phone = phone
    if payload.client_id:
        user.oasys_client_id = payload.client_id
    db.add(user)
    db.commit()
    return {"status": "linked", "telegram_id": user.telegram_id}
