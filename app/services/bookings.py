"""Приём броней из OASys (вебхук ещё не подключён — см. README «Что нужно
от команды OASys»). Структура повторяет sessions.py: идемпотентность по
(club_id, external_booking_id), гость резолвится тем же приоритетом
client_id -> telegram_id -> phone. Засчитывает ачивку week_booked_play,
когда статус брони становится "completed".
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Booking, Club, User
from app.periods import ensure_utc
from app.services import achievements
from app.services.sessions import resolve_user

PLAYED_STATUSES = {"completed"}
ACHIEVEMENT_CODE = "week_booked_play"


class BookingIngestError(Exception):
    pass


class UserNotLinked(BookingIngestError):
    """Бронь пришла на гостя, который ещё не привязал Telegram к программе."""


def _find(db: Session, club_id: int, external_booking_id: str) -> Booking | None:
    return db.execute(
        select(Booking).where(
            Booking.club_id == club_id,
            Booking.external_booking_id == external_booking_id,
        )
    ).scalar_one_or_none()


def _credit_played(db: Session, user: User) -> None:
    achievements.increment(db, user, ACHIEVEMENT_CODE, 1)


def ingest(db: Session, club: Club, payload) -> tuple[Booking, bool]:
    """Идемпотентно: повторный вебхук с тем же booking_id в рамках клуба
    только обновляет статус существующей строки, не создаёт вторую и не
    начисляет прогресс дважды за одну и ту же бронь."""
    existing = _find(db, club.id, payload.booking_id)
    if existing is not None:
        _apply_status(db, existing, payload.status)
        return existing, False

    user = resolve_user(
        db,
        telegram_id=payload.telegram_id,
        phone=payload.phone,
        oasys_client_id=payload.client_id,
    )
    if user is None:
        raise UserNotLinked("гость не привязан к программе лояльности")

    row = Booking(
        user_id=user.id,
        club_id=club.id,
        external_booking_id=payload.booking_id,
        status=payload.status,
        scheduled_start=ensure_utc(payload.scheduled_start) if payload.scheduled_start else None,
        scheduled_end=ensure_utc(payload.scheduled_end) if payload.scheduled_end else None,
        pc_number=payload.pc_number,
        price=payload.price,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = _find(db, club.id, payload.booking_id)
        if existing is None:
            raise BookingIngestError(f"Не удалось сохранить бронь {payload.booking_id}") from None
        return existing, False

    if payload.status in PLAYED_STATUSES:
        _credit_played(db, user)

    return row, True


def _apply_status(db: Session, row: Booking, status: str) -> None:
    if row.status == status:
        return
    was_played = row.status in PLAYED_STATUSES
    row.status = status
    db.add(row)
    db.flush()
    if status in PLAYED_STATUSES and not was_played:
        user = db.get(User, row.user_id)
        _credit_played(db, user)
