"""Реферальная система.

Приглашение засчитывается не в момент перехода по ссылке, а когда приведённый
друг реально отыграл минимум (по умолчанию час) — иначе ссылку легко накрутить.
"""

import secrets
import string

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User
from app.services import achievements, sessions

settings = get_settings()

REFERRAL_ACHIEVEMENT = "special_referral"
_ALPHABET = string.ascii_uppercase + string.digits


class ReferralError(Exception):
    pass


def generate_code(db: Session) -> str:
    while True:
        code = "".join(secrets.choice(_ALPHABET) for _ in range(8))
        exists = db.execute(select(User.id).where(User.referral_code == code)).first()
        if not exists:
            return code


def find_by_code(db: Session, code: str) -> User | None:
    if not code:
        return None
    return db.execute(
        select(User).where(User.referral_code == code.strip().upper())
    ).scalar_one_or_none()


def attach(db: Session, user: User, code: str) -> bool:
    """Привязывает пригласившего. Возвращает False, если привязка невозможна."""
    inviter = find_by_code(db, code)
    if inviter is None or inviter.id == user.id:
        return False
    if user.referred_by_id is not None:
        return False
    # Ссылка работает только для новичка: если гость уже играл, он не «приведённый».
    if sessions.total_minutes(db, user.id) > 0:
        return False

    user.referred_by_id = inviter.id
    db.add(user)
    db.flush()
    return True


def on_session_closed(db: Session, user: User) -> None:
    """Проверяет, не пора ли засчитать приглашение пригласившему."""
    if user.referred_by_id is None or user.referral_credited:
        return
    if sessions.total_minutes(db, user.id) < settings.referral_min_minutes:
        return

    inviter = db.get(User, user.referred_by_id)
    if inviter is None:
        return

    user.referral_credited = True
    db.add(user)
    achievements.mark_completed(db, inviter, REFERRAL_ACHIEVEMENT)
    db.flush()


def referral_link(code: str) -> str:
    """Ссылка ведёт в бота: он ловит /start CODE, привязывает пригласившего
    и сам открывает мини-апп. Прямая ссылка на мини-апп не даёт боту
    шанса поздороваться и попросить телефон."""
    if settings.bot_username:
        return f"https://t.me/{settings.bot_username}?start={code}"
    return f"?start={code}"


def summary(db: Session, user: User) -> dict:
    invited = list(
        db.execute(select(User).where(User.referred_by_id == user.id)).scalars()
    )
    return {
        "code": user.referral_code,
        "invited_total": len(invited),
        "invited_credited": sum(1 for u in invited if u.referral_credited),
        "min_minutes": settings.referral_min_minutes,
        "link": referral_link(user.referral_code),
    }
