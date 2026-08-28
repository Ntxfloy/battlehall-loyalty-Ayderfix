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

from pydantic import BaseModel

from app.config import is_placeholder_secret, secrets_match
from app.db import get_db
from app.models import Club, User, WebhookInbox
from app.schemas import (
    BalanceOperationPayload,
    BookingPayload,
    LinkPhonePayload,
    PurchasePayload,
    SessionEndPayload,
    SessionStartPayload,
)
from app.services import bookings, clubs, oasys_ledger, purchases, referrals, sessions

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


def _log_inbox(db: Session, club: Club, endpoint: str, payload: BaseModel, status: str) -> None:
    """Пишет сырое событие и коммитит его ДО попытки обработки: на любой
    спор «что именно прислал OASys» есть однозначный ответ, даже если сама
    обработка ниже упадёт и откатится (Roadmap/СТАТУС.md, п.4)."""
    db.add(WebhookInbox(club_id=club.id, endpoint=endpoint, raw_body=payload.model_dump_json(), status=status))
    db.commit()


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


@router.post("/{club_slug}/booking")
def booking_webhook(
    club_slug: str,
    payload: BookingPayload,
    x_oasys_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """ПРЕДЛОЖЕННЫЙ контракт — OASys брони пока не шлёт (см. README
    «Что нужно от команды OASys»). Роут заведён заранее, чтобы было что
    показать/протестировать до их ответа."""
    club = _resolve_club(db, club_slug, x_oasys_token)
    _log_inbox(db, club, "booking", payload, status="received")
    try:
        row, created = bookings.ingest(db, club, payload)
    except bookings.UserNotLinked:
        logger.info("club %s, booking %s: гость не привязан, пропускаем", club_slug, payload.booking_id)
        return {"status": "skipped", "reason": "user_not_linked"}
    except bookings.BookingIngestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.commit()
    return {"status": "created" if created else "updated", "booking_id": row.external_booking_id}


@router.post("/{club_slug}/purchase")
def purchase_webhook(
    club_slug: str,
    payload: PurchasePayload,
    x_oasys_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """ПРЕДЛОЖЕННЫЙ контракт — OASys покупки пакетов часов пока не шлёт."""
    club = _resolve_club(db, club_slug, x_oasys_token)
    _log_inbox(db, club, "purchase", payload, status="received")
    try:
        row, created = purchases.ingest(db, club, payload)
    except purchases.UserNotLinked:
        logger.info("club %s, purchase %s: гость не привязан, пропускаем", club_slug, payload.purchase_id)
        return {"status": "skipped", "reason": "user_not_linked"}
    except purchases.PurchaseIngestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.commit()
    return {"status": "created" if created else "duplicate", "purchase_id": row.external_purchase_id}


@router.post("/{club_slug}/balance-operation")
def balance_operation_webhook(
    club_slug: str,
    payload: BalanceOperationPayload,
    x_oasys_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """ПРЕДЛОЖЕННЫЙ контракт — замена пуллингу operations/history из
    oasys_live.py. OASys пока не шлёт, роут заведён заранее для демонстрации."""
    club = _resolve_club(db, club_slug, x_oasys_token)
    _log_inbox(db, club, "balance-operation", payload, status="received")
    try:
        row, created = oasys_ledger.ingest(db, club, payload)
    except oasys_ledger.UserNotLinked:
        logger.info("club %s, operation %s: гость не привязан, пропускаем", club_slug, payload.operation_id)
        return {"status": "skipped", "reason": "user_not_linked"}
    except oasys_ledger.BalanceOperationIngestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.commit()
    return {"status": "created" if created else "duplicate", "operation_id": row.external_operation_id}


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
