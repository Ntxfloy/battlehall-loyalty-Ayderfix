"""Каталог наград и обмен PTS на код компенсации.

Схема из спеки: гость обменивает PTS -> получает код, код виден только внутри
мини-аппа и живёт 24 часа, админ гасит его на стойке и переносит строку
в гугл-таблицу компенсаций.
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import RedemptionStatus, Reward, RewardRedemption, TxReason, User
from app.periods import ensure_utc
from app.services import achievements, pts

logger = logging.getLogger(__name__)
settings = get_settings()

_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LEN = 8


class RewardError(Exception):
    pass


def _generate_code(db: Session) -> str:
    while True:
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LEN))
        exists = db.execute(
            select(RewardRedemption.id).where(RewardRedemption.code == code)
        ).first()
        if not exists:
            return code


def catalog(db: Session) -> list[Reward]:
    return list(
        db.execute(
            select(Reward).where(Reward.is_active.is_(True)).order_by(Reward.sort_order)
        ).scalars()
    )


def redeem(db: Session, user: User, reward_id: int) -> RewardRedemption:
    reward = db.get(Reward, reward_id)
    if reward is None or not reward.is_active:
        raise RewardError("Награда недоступна")

    active = active_redemption(db, user)
    if active is not None:
        raise RewardError("У тебя уже есть активный код — сначала используй его")

    try:
        pts.debit(
            db,
            user,
            reward.cost_pts,
            reason=TxReason.REWARD_REDEEM,
            ref_type="reward",
            ref_id=str(reward.id),
            comment=reward.title,
        )
    except pts.InsufficientFunds as exc:
        raise RewardError(f"Не хватает PTS: {exc}") from exc

    return issue_code(db, user, reward, pts_spent=reward.cost_pts)


def issue_code(
    db: Session,
    user: User,
    reward: Reward,
    pts_spent: int = 0,
    source: str = "catalog",
) -> RewardRedemption:
    now = datetime.now(timezone.utc)
    redemption = RewardRedemption(
        user_id=user.id,
        reward_id=reward.id,
        code=_generate_code(db),
        status=RedemptionStatus.PENDING,
        pts_spent=pts_spent,
        reward_title=reward.title,
        payout_value=reward.payout_value,
        payout_unit=reward.payout_unit,
        created_at=now,
        expires_at=now + timedelta(hours=settings.reward_code_ttl_hours),
        source=source,
    )
    db.add(redemption)
    db.flush()
    return redemption


def active_redemption(db: Session, user: User) -> RewardRedemption | None:
    now = datetime.now(timezone.utc)
    return db.execute(
        select(RewardRedemption)
        .where(
            RewardRedemption.user_id == user.id,
            RewardRedemption.status == RedemptionStatus.PENDING,
            RewardRedemption.expires_at > now,
        )
        .order_by(RewardRedemption.created_at.desc())
    ).scalars().first()


def history(db: Session, user: User, limit: int = 50) -> list[RewardRedemption]:
    return list(
        db.execute(
            select(RewardRedemption)
            .where(RewardRedemption.user_id == user.id)
            .order_by(RewardRedemption.created_at.desc())
            .limit(limit)
        ).scalars()
    )


def effective_status(row: RewardRedemption, now: datetime | None = None) -> str:
    if now is None:
        now = datetime.now(timezone.utc)
    if row.status == RedemptionStatus.PENDING and row.expires_at and ensure_utc(row.expires_at) <= now:
        return RedemptionStatus.EXPIRED
    return row.status


def _conflict_message(row: RewardRedemption) -> str:
    status = effective_status(row)
    if status == RedemptionStatus.APPROVED:
        return "Код уже подтверждён другим администратором"
    if status == RedemptionStatus.SUBMITTED:
        used = ensure_utc(row.used_at)
        used_str = f"{used:%d.%m.%Y %H:%M}" if used else "ранее"
        return f"Код уже внесён {used_str} ({row.used_by or 'сотрудником'})"
    if status == RedemptionStatus.EXPIRED:
        return "Срок действия кода истёк"
    if status == RedemptionStatus.CANCELLED:
        return "Код отменён"
    return "Код уже обработан в другой сессии"


def expire_due(db: Session, *, user_id: int | None = None, limit: int = 200) -> int:
    now = datetime.now(timezone.utc)
    settings = get_settings()

    stmt = (
        select(RewardRedemption)
        .where(
            RewardRedemption.status == RedemptionStatus.PENDING,
            RewardRedemption.expires_at.isnot(None),
            RewardRedemption.expires_at <= now,
        )
        .order_by(RewardRedemption.expires_at.asc())
        .limit(limit)
    )
    if user_id is not None:
        stmt = stmt.where(RewardRedemption.user_id == user_id)

    candidates = list(db.execute(stmt).scalars())
    expired = 0
    for row in candidates:
        result = db.execute(
            update(RewardRedemption)
            .where(
                RewardRedemption.id == row.id,
                RewardRedemption.status == RedemptionStatus.PENDING,
            )
            .values(status=RedemptionStatus.EXPIRED, expired_at=now)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            continue
        expired += 1
        if not settings.refund_pts_on_expire or row.pts_spent <= 0:
            continue
        refund_marked = db.execute(
            update(RewardRedemption)
            .where(
                RewardRedemption.id == row.id,
                RewardRedemption.refunded_at.is_(None),
            )
            .values(refunded_at=now)
            .execution_options(synchronize_session=False)
        )
        if refund_marked.rowcount != 1:
            continue
        user = db.get(User, row.user_id)
        if user is None:
            logger.error("Код %s истёк, но гость %s не найден: возврат PTS не выполнен", row.code, row.user_id)
            continue
        pts.credit(
            db,
            user,
            row.pts_spent,
            reason=TxReason.REWARD_REFUND,
            ref_type="redemption",
            ref_id=row.code,
            comment=f"Возврат за сгоревший код {row.code}",
        )
    return expired


def use_code(db: Session, code: str, admin: str) -> RewardRedemption:
    normalized = code.strip().upper()
    row = db.execute(
        select(RewardRedemption).where(RewardRedemption.code == normalized)
    ).scalar_one_or_none()
    if row is None:
        raise RewardError("Код не найден")
    if row.status != RedemptionStatus.PENDING:
        raise RewardError(_conflict_message(row))
    now = datetime.now(timezone.utc)
    result = db.execute(
        update(RewardRedemption)
        .where(
            RewardRedemption.id == row.id,
            RewardRedemption.status == RedemptionStatus.PENDING,
            RewardRedemption.expires_at > now,
        )
        .values(status=RedemptionStatus.SUBMITTED, used_at=now, used_by=admin)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.expire(row)
        fresh = lookup(db, code)
        raise RewardError(_conflict_message(fresh or row))
    db.refresh(row)
    return row


def approve_code(db: Session, code: str, admin: str) -> RewardRedemption:
    row = lookup(db, code)
    if row is None:
        raise RewardError("Код не найден")
    if row.status == RedemptionStatus.APPROVED:
        raise RewardError("Код уже подтверждён")
    if row.status != RedemptionStatus.SUBMITTED:
        raise RewardError("Подтверждать можно только внесённый на стойке код")
    now = datetime.now(timezone.utc)
    result = db.execute(
        update(RewardRedemption)
        .where(
            RewardRedemption.id == row.id,
            RewardRedemption.status == RedemptionStatus.SUBMITTED,
        )
        .values(status=RedemptionStatus.APPROVED, approved_at=now, approved_by=admin)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.expire(row)
        fresh = lookup(db, code)
        raise RewardError(_conflict_message(fresh or row))
    db.refresh(row)
    return row


def _refund_if_needed(db: Session, row: RewardRedemption, now: datetime) -> None:
    local = get_settings()
    if not local.refund_pts_on_expire or row.pts_spent <= 0:
        return
    refund_marked = db.execute(
        update(RewardRedemption)
        .where(
            RewardRedemption.id == row.id,
            RewardRedemption.refunded_at.is_(None),
        )
        .values(refunded_at=now)
        .execution_options(synchronize_session=False)
    )
    if refund_marked.rowcount != 1:
        return
    user = db.get(User, row.user_id)
    if user is None:
        logger.error("Код %s отклонён как истёкший, гость %s не найден", row.code, row.user_id)
        return
    pts.credit(
        db,
        user,
        row.pts_spent,
        reason=TxReason.REWARD_REFUND,
        ref_type="redemption",
        ref_id=row.code,
        comment=f"Возврат за отклонённый истёкший код {row.code}",
    )


def reject_code(db: Session, code: str, admin: str) -> RewardRedemption:
    """Отклонение внесённого кода. Если срок уже вышел — гасим и возвращаем PTS."""
    row = lookup(db, code)
    if row is None:
        raise RewardError("Код не найден")
    if row.status != RedemptionStatus.SUBMITTED:
        raise RewardError("Отклонить можно только внесённый на стойке код")
    now = datetime.now(timezone.utc)
    still_valid = ensure_utc(row.expires_at) > now
    new_status = RedemptionStatus.PENDING if still_valid else RedemptionStatus.EXPIRED
    result = db.execute(
        update(RewardRedemption)
        .where(
            RewardRedemption.id == row.id,
            RewardRedemption.status == RedemptionStatus.SUBMITTED,
        )
        .values(
            status=new_status,
            used_at=None,
            used_by=None,
            expired_at=now if not still_valid else None,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.expire(row)
        fresh = lookup(db, code)
        raise RewardError(_conflict_message(fresh or row))
    db.refresh(row)
    if new_status == RedemptionStatus.EXPIRED:
        _refund_if_needed(db, row, now)
        db.refresh(row)
    return row


def pending_approval(db: Session, limit: int = 200) -> list[RewardRedemption]:
    return list(
        db.execute(
            select(RewardRedemption)
            .where(RewardRedemption.status == RedemptionStatus.SUBMITTED)
            .order_by(RewardRedemption.used_at)
            .limit(limit)
        ).scalars()
    )


def lookup(db: Session, code: str) -> RewardRedemption | None:
    return db.execute(
        select(RewardRedemption).where(RewardRedemption.code == code.strip().upper())
    ).scalar_one_or_none()


def grant_pts(db: Session, user: User, amount: int, comment: str) -> None:
    pts.credit(db, user, amount, reason=TxReason.MANUAL, comment=comment)
    achievements.on_pts_changed(db, user)
