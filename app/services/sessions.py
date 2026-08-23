"""Приём игровых сессий из OASys и статистика по ним.

Каждая сессия принадлежит конкретному клубу сети (`Club`) — это нужно и для
идемпотентности (session_id уникален только в рамках одного клуба), и для
отчётности по клубам в админке.
"""

import re
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Club, GameSession, User
from app.periods import ensure_utc, game_day
from app.services import achievements
from app.zones import parse_overrides, zone_for_pc_in_club

settings = get_settings()


class SessionIngestError(Exception):
    pass


class UserNotLinked(SessionIngestError):
    """Сессия пришла на гостя, который ещё не привязал Telegram к программе."""


def normalize_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    return digits or None


def resolve_user(
    db: Session,
    *,
    telegram_id: int | None = None,
    phone: str | None = None,
    oasys_client_id: str | None = None,
) -> User | None:
    """Ищем гостя по любому из идентификаторов, которые умеет присылать OASys."""
    if oasys_client_id:
        user = db.execute(
            select(User).where(User.oasys_client_id == oasys_client_id)
        ).scalar_one_or_none()
        if user:
            return user
    if telegram_id:
        user = db.execute(
            select(User).where(User.telegram_id == telegram_id)
        ).scalar_one_or_none()
        if user:
            return user
    normalized = normalize_phone(phone)
    if normalized:
        user = db.execute(select(User).where(User.phone == normalized)).scalar_one_or_none()
        if user:
            # доучиваем связку, чтобы следующие вебхуки резолвились сразу по client_id
            if oasys_client_id and not user.oasys_client_id:
                user.oasys_client_id = oasys_client_id
                db.add(user)
            return user
    return None


def _new_session(db: Session, user: User, club: Club, payload) -> GameSession:
    overrides = parse_overrides(club.pc_zone_overrides)
    zone = zone_for_pc_in_club(payload.pc_number, overrides)
    if zone is None:
        raise SessionIngestError(f"ПК {payload.pc_number} не найден в карте зон клуба «{club.name}»")

    # Вебхук может прислать время без смещения, а SQLite отдаёт naive-даты
    # при чтении. Приводим всё к UTC на границе, дальше внутри только UTC.
    started_at = ensure_utc(payload.started_at)
    row = GameSession(
        user_id=user.id,
        club_id=club.id,
        oasys_session_id=payload.session_id,
        pc_number=payload.pc_number,
        zone_code=zone.code,
        zone_type=zone.zone_type,
        started_at=started_at,
        game_day=game_day(started_at, settings.game_day_start_hour).isoformat(),
    )
    db.add(row)
    db.flush()
    return row


def _find(db: Session, club_id: int, session_id: str) -> GameSession | None:
    return db.execute(
        select(GameSession).where(
            GameSession.club_id == club_id,
            GameSession.oasys_session_id == session_id,
        )
    ).scalar_one_or_none()


def start_session(db: Session, club: Club, payload) -> tuple[GameSession, bool]:
    """Идемпотентно: повторный вебхук с тем же session_id в рамках клуба ничего не меняет."""
    existing = _find(db, club.id, payload.session_id)
    if existing is not None:
        return existing, False

    user = resolve_user(
        db,
        telegram_id=payload.telegram_id,
        phone=payload.phone,
        oasys_client_id=payload.client_id,
    )
    if user is None:
        raise UserNotLinked("гость не привязан к программе лояльности")

    row = _new_session(db, user, club, payload)
    achievements.on_session_started(db, user, row)
    return row, True


def end_session(db: Session, club: Club, payload) -> tuple[GameSession, bool]:
    row = _find(db, club.id, payload.session_id)

    if row is None:
        # Вебхук о старте потеряли — восстанавливаем сессию из события конца.
        user = resolve_user(
            db,
            telegram_id=payload.telegram_id,
            phone=payload.phone,
            oasys_client_id=payload.client_id,
        )
        if user is None:
            raise UserNotLinked("гость не привязан к программе лояльности")
        row = _new_session(db, user, club, payload)
        achievements.on_session_started(db, user, row)
    else:
        user = db.get(User, row.user_id)

    if row.is_closed:
        return row, False

    ended_at = ensure_utc(payload.ended_at) if payload.ended_at else datetime.now(timezone.utc)
    minutes = payload.duration_minutes
    if minutes is None:
        minutes = max(int((ended_at - ensure_utc(row.started_at)).total_seconds() // 60), 0)

    row.ended_at = ended_at
    row.duration_minutes = minutes
    row.is_closed = True
    db.add(row)
    db.flush()

    achievements.on_session_ended(db, user, row)

    from app.services import referrals  # локальный импорт: избегаем цикла

    referrals.on_session_closed(db, user)

    return row, True



# --- статистика для гостя ---

def year_stats(db: Session, user_id: int, year: int) -> dict:
    prefix = f"{year}-"
    rows = list(
        db.execute(
            select(GameSession.game_day, GameSession.duration_minutes).where(
                GameSession.user_id == user_id,
                GameSession.game_day.like(f"{prefix}%"),
            )
        )
    )
    minutes = sum(r.duration_minutes for r in rows)
    return {
        "year": year,
        "visits": len({r.game_day for r in rows}),
        "sessions": len(rows),
        "minutes": minutes,
        "hours": round(minutes / 60, 1),
    }


def visit_history(db: Session, user_id: int, year: int | None = None, limit: int = 200) -> list[GameSession]:
    stmt = select(GameSession).where(GameSession.user_id == user_id)
    if year is not None:
        stmt = stmt.where(GameSession.game_day.like(f"{year}-%"))
    stmt = stmt.order_by(GameSession.started_at.desc()).limit(limit)
    return list(db.execute(stmt).scalars())


def available_years(db: Session, user_id: int) -> list[int]:
    rows = db.execute(
        select(func.substr(GameSession.game_day, 1, 4))
        .where(GameSession.user_id == user_id)
        .distinct()
    ).scalars()
    return sorted({int(y) for y in rows if y}, reverse=True)


def total_minutes(db: Session, user_id: int) -> int:
    value = db.execute(
        select(func.coalesce(func.sum(GameSession.duration_minutes), 0)).where(
            GameSession.user_id == user_id
        )
    ).scalar_one()
    return int(value)
