"""Приём событий из OASys.

Путь содержит slug клуба: `/api/webhooks/oasys/{club_slug}/session-start`.
Так у каждого клуба сети свой токен и своя идемпотентность `session_id`,
не завязанные на конфиг сервера. Токен — в заголовке `X-OASys-Token`,
сверяется с тем, что хранится в таблице clubs.
"""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import is_placeholder_secret, secrets_match
from app.db import get_db
from app.models import Club, User
from app.schemas import LinkPhonePayload, SessionEndPayload, SessionStartPayload
from app.services import clubs, referrals, sessions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks/oasys", tags=["webhooks"])


def _resolve_club(db: Session, slug: str, token: str | None) -> Club:
    club = clubs.get_by_slug(db, slug)
    if club is None or not club.is_active:
        raise HTTPException(status_code=404, detail="Клуб не найден")

    if is_placeholder_secret(club.oasys_webhook_token):
        logger.error("Webhook rejected for club %s: unsafe stored token", club.slug)
        raise HTTPException(status_code=401, detail="Неверный токен вебхука")

    if not secrets_match(token, club.oasys_webhook_token):
        raise HTTPException(status_code=401, detail="Неверный токен вебхука")

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
        logger.info("club %s, session %s: гость не привязан, пропускаем", club_slug, payload.session_id)
        return {"status": "skipped", "reason": "user_not_linked"}
    except sessions.SessionIngestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.commit()
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

    db.commit()
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
    чтобы матчить вебхуки сессий на пользователя программы."""
    _resolve_club(db, club_slug, x_oasys_token)

    phone = sessions.normalize_phone(payload.phone)
    if not phone:
        raise HTTPException(status_code=400, detail="Некорректный телефон")

    owner = db.query(User).filter(User.phone == phone).one_or_none()
    user = db.query(User).filter(User.telegram_id == payload.telegram_id).one_or_none()

    if owner is not None and (user is None or owner.id != user.id):
        raise HTTPException(status_code=409, detail="Телефон уже привязан к другому аккаунту")

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
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Телефон уже привязан к другому аккаунту") from exc
    return {"status": "linked", "telegram_id": user.telegram_id}
