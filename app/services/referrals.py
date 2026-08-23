"""Реферальная система.

Приглашение засчитывается не в момент перехода по ссылке, а когда приведённый
друг реально отыграл минимум (по умолчанию час) — иначе ссылку легко накрутить.
"""

import logging
import secrets

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User
from app.services import achievements, sessions

logger = logging.getLogger(__name__)
settings = get_settings()

REFERRAL_ACHIEVEMENT = "special_referral"
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_ATTEMPTS = 32
_MAX_CHAIN_DEPTH = 100


class ReferralError(Exception):
    pass


def generate_code(db: Session) -> str:
    for _ in range(_CODE_ATTEMPTS):
        code = "".join(secrets.choice(_ALPHABET) for _ in range(8))
        exists = db.execute(select(User.id).where(User.referral_code == code)).first()
        if not exists:
            return code
    raise ReferralError("Не удалось создать уникальный реферальный код")


def find_by_code(db: Session, code: str) -> User | None:
    if not code:
        return None
    return db.execute(
        select(User).where(User.referral_code == code.strip().upper())
    ).scalar_one_or_none()


def _would_create_cycle(db: Session, user: User, inviter: User) -> bool:
    """Запрещает A→B→…→A, а не только прямое взаимное приглашение."""
    current = inviter
    seen: set[int] = set()
    for _ in range(_MAX_CHAIN_DEPTH):
        if current.id == user.id:
            return True
        if current.id in seen or current.referred_by_id is None:
            return False
        seen.add(current.id)
        parent = db.get(User, current.referred_by_id)
        if parent is None:
            return False
        current = parent
    logger.warning("Слишком глубокая реферальная цепочка у пользователя %s", inviter.id)
    return True


def attach(db: Session, user: User, code: str) -> bool:
    inviter = find_by_code(db, code)
    if inviter is None or inviter.id == user.id:
        return False
    if user.referred_by_id is not None:
        return False
    if sessions.total_minutes(db, user.id) > 0:
        return False
    if _would_create_cycle(db, user, inviter):
        return False
    user.referred_by_id = inviter.id
    db.add(user)
    db.flush()
    return True


def on_session_closed(db: Session, user: User) -> None:
    if user.referred_by_id is None or user.referral_credited:
        return
    if sessions.total_minutes(db, user.id) < settings.referral_min_minutes:
        return
    inviter = db.get(User, user.referred_by_id)
    if inviter is None:
        return
    user.referral_credited = True
    db.add(user)
    try:
        achievements.increment(db, inviter, REFERRAL_ACHIEVEMENT)
    except achievements.AchievementError:
        logger.warning(
            "Реферал засчитан пользователю %s, но ачивка %s недоступна",
            inviter.id,
            REFERRAL_ACHIEVEMENT,
        )
    db.flush()


def referral_link(code: str) -> str:
    if settings.bot_username:
        return "https://t.me/" + settings.bot_username + "?start=" + code
    return "?start=" + code


def summary(db: Session, user: User) -> dict:
    invited_total, invited_credited = db.execute(
        select(
            func.count(User.id),
            func.coalesce(func.sum(cast(User.referral_credited, Integer)), 0),
        ).where(User.referred_by_id == user.id)
    ).one()
    return {
        "code": user.referral_code,
        "invited_total": int(invited_total or 0),
        "invited_credited": int(invited_credited or 0),
        "min_minutes": settings.referral_min_minutes,
        "link": referral_link(user.referral_code),
    }
