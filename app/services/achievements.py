"""Движок достижений.

Слушает события игровых сессий (они приезжают вебхуками из OASys), ведёт
прогресс по периодам и отдаёт готовую картину во вкладку «Достижения».

Два правила, которые важно держать в голове:
  * прогресс живёт в разрезе периода (`period_key`), поэтому «обнуления» как
    отдельной джобы нет — в понедельник в 06:00 МСК просто начинает считаться
    строка с новым ключом;
  * PTS за выполненную ачивку начисляются только когда гость нажал «Забрать».
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import achievements_defs as defs
from app.achievements_defs import BY_CODE
from app.config import get_settings
from app.models import (
    AchievementDef,
    AchievementProgress,
    GameSession,
    TxReason,
    User,
)
from app.periods import period_ends_at, period_key
from app.services import pts

settings = get_settings()

MARK_SEP = ","


class AchievementError(Exception):
    pass


# --- вспомогательное ---

def _rule_for(code: str) -> str:
    spec = BY_CODE.get(code)
    return spec.rule if spec else defs.RULE_EXTERNAL


def _active_defs(db: Session, rule: str | None = None) -> list[AchievementDef]:
    rows = list(
        db.execute(
            select(AchievementDef)
            .where(AchievementDef.is_active.is_(True))
            .order_by(AchievementDef.sort_order)
        ).scalars()
    )
    if rule is None:
        return rows
    return [d for d in rows if d.is_implemented and _rule_for(d.code) == rule]


def _current_key(adef: AchievementDef, at: datetime) -> str:
    return period_key(adef.period, at, settings.game_day_start_hour)


def _find_progress(db: Session, user_id: int, adef: AchievementDef, at: datetime) -> AchievementProgress | None:
    key = _current_key(adef, at)
    return db.execute(
        select(AchievementProgress).where(
            AchievementProgress.user_id == user_id,
            AchievementProgress.achievement_code == adef.code,
            AchievementProgress.period_key == key,
        )
    ).scalar_one_or_none()


def _get_progress(db: Session, user_id: int, adef: AchievementDef, at: datetime) -> AchievementProgress:
    row = _find_progress(db, user_id, adef, at)
    if row is None:
        key = _current_key(adef, at)
        row = AchievementProgress(
            user_id=user_id,
            achievement_code=adef.code,
            period_key=key,
            progress=0,
            target=adef.target,
            reward_pts=adef.reward_pts,
            marks="",
        )
        db.add(row)
        db.flush()
    return row



def _marks(row: AchievementProgress) -> set[str]:
    return {m for m in (row.marks or "").split(MARK_SEP) if m}


def _touch(row: AchievementProgress) -> None:
    now = datetime.now(timezone.utc)
    row.updated_at = now
    if row.progress >= row.target and row.completed_at is None:
        row.completed_at = now


def _add_mark(db: Session, row: AchievementProgress, mark: str) -> bool:
    """Отмечает уникальное событие (день, тип зоны). Возвращает True, если отметка новая."""
    marks = _marks(row)
    if mark in marks:
        return False
    marks.add(mark)
    row.marks = MARK_SEP.join(sorted(marks))
    row.progress = min(len(marks), row.target)
    _touch(row)
    db.add(row)
    return True


def _bump(db: Session, row: AchievementProgress, delta: int) -> None:
    row.progress = min(row.progress + delta, row.target)
    _touch(row)
    db.add(row)


def _set(db: Session, row: AchievementProgress, value: int) -> None:
    row.progress = min(value, row.target)
    _touch(row)
    db.add(row)


# --- реакции на события ---

def on_session_started(db: Session, user: User, session: GameSession) -> None:
    at = session.started_at

    for adef in _active_defs(db, defs.RULE_DAILY_CHECKIN):
        _set(db, _get_progress(db, user.id, adef, at), adef.target)

    for adef in _active_defs(db, defs.RULE_DISTINCT_DAYS):
        _add_mark(db, _get_progress(db, user.id, adef, at), session.game_day)

    for adef in _active_defs(db, defs.RULE_DISTINCT_WEEKDAYS):
        weekday = datetime.fromisoformat(session.game_day).isoweekday()
        _add_mark(db, _get_progress(db, user.id, adef, at), str(weekday))

    for adef in _active_defs(db, defs.RULE_DISTINCT_ZONE_TYPES):
        _add_mark(db, _get_progress(db, user.id, adef, at), session.zone_type)

    for adef in _active_defs(db, defs.RULE_SESSIONS):
        _bump(db, _get_progress(db, user.id, adef, at), 1)

    db.flush()


def on_session_ended(db: Session, user: User, session: GameSession) -> None:
    # Минуты вешаем на период старта сессии: ночная игра целиком относится
    # к тому игровому дню, в который гость сел за ПК.
    at = session.started_at
    for adef in _active_defs(db, defs.RULE_MINUTES):
        _bump(db, _get_progress(db, user.id, adef, at), session.duration_minutes)
    db.flush()


def on_pts_changed(db: Session, user: User) -> None:
    """Пересчитывает накопительные ачивки по всем заработанным PTS."""
    earned = pts.total_earned(db, user.id)
    now = datetime.now(timezone.utc)
    for adef in _active_defs(db, defs.RULE_PTS_EARNED):
        _set(db, _get_progress(db, user.id, adef, now), earned)
    db.flush()


def mark_completed(db: Session, user: User, code: str, at: datetime | None = None) -> AchievementProgress:
    """Отмечает разовую ачивку выполненной: подписка на канал, реферал, опрос.
    PTS не начисляет — гость забирает награду сам."""
    at = at or datetime.now(timezone.utc)
    adef = db.execute(
        select(AchievementDef).where(AchievementDef.code == code)
    ).scalar_one_or_none()
    if adef is None:
        raise AchievementError(f"нет такой ачивки: {code}")
    row = _get_progress(db, user.id, adef, at)
    _set(db, row, adef.target)
    db.flush()
    return row


def increment(db: Session, user: User, code: str, delta: int = 1, at: datetime | None = None) -> AchievementProgress:
    """Ручной инкремент — точка входа для будущих интеграций (бар, брони, пакеты часов)."""
    at = at or datetime.now(timezone.utc)
    adef = db.execute(
        select(AchievementDef).where(AchievementDef.code == code)
    ).scalar_one_or_none()
    if adef is None:
        raise AchievementError(f"нет такой ачивки: {code}")
    row = _get_progress(db, user.id, adef, at)
    _bump(db, row, delta)
    db.flush()
    return row


# --- забрать награду ---

def claim(db: Session, user: User, code: str) -> AchievementProgress:
    now = datetime.now(timezone.utc)
    adef = db.execute(
        select(AchievementDef).where(AchievementDef.code == code)
    ).scalar_one_or_none()
    if adef is None:
        raise AchievementError("Достижение не найдено")

    key = _current_key(adef, now)
    row = db.execute(
        select(AchievementProgress).where(
            AchievementProgress.user_id == user.id,
            AchievementProgress.achievement_code == code,
            AchievementProgress.period_key == key,
        )
    ).scalar_one_or_none()

    if row is None or row.completed_at is None:
        raise AchievementError("Достижение ещё не выполнено")
    if row.claimed_at is not None:
        raise AchievementError("Награда за это достижение уже забрана")

    row.claimed_at = now
    db.add(row)

    # Ачивка может быть имиджевой и не давать PTS. Раньше такая строка
    # уронила бы claim через ValueError в pts.credit — гость получал 500
    # вместо отметки ·забрано·.
    if row.reward_pts > 0:
        # Ключ идемпотентности привязан к строке прогресса (гость + ачивка + период):
        # даже если claimed_at когда-то собьют руками или два запроса придут
        # одновременно, PTS за одну и ту же ачивку начислятся ровно один раз.
        pts.credit(
            db,
            user,
            row.reward_pts,
            reason=TxReason.ACHIEVEMENT,
            ref_type="achievement",
            ref_id=f"{code}:{key}",
            comment=adef.title,
            idem_key=f"achievement_claim:{row.id}",
        )
        on_pts_changed(db, user)
    else:
        db.flush()

    return row


# --- витрина ---

def overview(db: Session, user: User, now: datetime | None = None) -> dict:
    """Собирает всё, что нужно вкладке «Достижения» и карточке на Главной."""
    now = now or datetime.now(timezone.utc)
    result: dict[str, list[dict]] = {"daily": [], "weekly": [], "monthly": [], "special": []}

    for adef in _active_defs(db):
        row = _find_progress(db, user.id, adef, now)
        ends_at = period_ends_at(adef.period, now, settings.game_day_start_hour)
        item = {
            "code": adef.code,
            "title": adef.title,
            "description": adef.description,
            "category": adef.category,
            "period": adef.period,
            "unit": adef.unit,
            "target": row.target if row else adef.target,
            "progress": row.progress if row else 0,
            "reward_pts": row.reward_pts if row else adef.reward_pts,
            "is_completed": (row.completed_at is not None) if row else False,
            "is_claimed": (row.claimed_at is not None) if row else False,
            "can_claim": (row.completed_at is not None and row.claimed_at is None) if row else False,
            "is_available": adef.is_implemented,
            "period_ends_at": ends_at.isoformat() if ends_at else None,
        }
        result.setdefault(adef.category, []).append(item)

    return result




def completed_count(db: Session, user: User) -> int:
    """Сколько достижений выполнено за всё время — цифра для Главной."""
    rows = db.execute(
        select(AchievementProgress).where(
            AchievementProgress.user_id == user.id,
            AchievementProgress.completed_at.is_not(None),
        )
    ).scalars()
    return sum(1 for _ in rows)
