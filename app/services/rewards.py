"""Каталог наград и обмен PTS на код компенсации.

Схема из спеки: гость обменивает PTS -> получает код, код виден только внутри
мини-аппа и живёт 24 часа, админ гасит его на стойке и переносит строку
в гугл-таблицу компенсаций.
"""

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import RedemptionStatus, Reward, RewardRedemption, TxReason, User
from app.periods import ensure_utc
from app.services import achievements, pts

settings = get_settings()

# Без 0/O/1/I — код читают вслух и вбивают руками на стойке
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

    # Одновременно активным держим только один код — чтобы на стойке
    # не было спора, какой из трёх кодов гость сейчас гасит.
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

    redemption = issue_code(db, user, reward, pts_spent=reward.cost_pts)
    db.commit()
    return redemption


def issue_code(
    db: Session,
    user: User,
    reward: Reward,
    pts_spent: int = 0,
    source: str = "catalog",
) -> RewardRedemption:
    """Выдаёт код на награду без списания PTS.

    Отдельно от `redeem`, потому что приз из «ЛУДЛЕНТЫ» уже оплачен
    прокруткой — списывать за него второй раз нельзя."""
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
    expire_due(db, user_id=user.id)
    return db.execute(
        select(RewardRedemption)
        .where(
            RewardRedemption.user_id == user.id,
            RewardRedemption.status == RedemptionStatus.PENDING,
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


def expire_due(db: Session, user_id: int | None = None) -> int:
    """Гасит просроченные коды. Если REFUND_PTS_ON_EXPIRE — возвращает PTS.

    Вызывается лениво при каждом обращении к наградам, поэтому отдельный
    планировщик для MVP не нужен.
    """
    now = datetime.now(timezone.utc)
    stmt = select(RewardRedemption).where(
        RewardRedemption.status == RedemptionStatus.PENDING,
        RewardRedemption.expires_at <= now,
    )
    if user_id is not None:
        stmt = stmt.where(RewardRedemption.user_id == user_id)

    rows = list(db.execute(stmt).scalars())
    for row in rows:
        row.status = RedemptionStatus.EXPIRED
        db.add(row)
        if settings.refund_pts_on_expire:
            user = db.get(User, row.user_id)
            if user is not None:
                pts.credit(
                    db,
                    user,
                    row.pts_spent,
                    reason=TxReason.REWARD_REFUND,
                    ref_type="redemption",
                    ref_id=row.code,
                    comment=f"Возврат за сгоревший код {row.code}",
                )
    if rows:
        db.commit()
    return len(rows)


def use_code(db: Session, code: str, admin: str) -> RewardRedemption:
    """Сотрудник вносит код на стойке. Код уходит в статус «ждёт подтверждения»:
    начисление гостю подтверждает владелец, сотрудник сам себя не аппрувит."""
    row = db.execute(
        select(RewardRedemption).where(RewardRedemption.code == code.strip().upper())
    ).scalar_one_or_none()
    if row is None:
        raise RewardError("Код не найден")
    if row.status in (RedemptionStatus.SUBMITTED, RedemptionStatus.APPROVED):
        used = ensure_utc(row.used_at)
        raise RewardError(f"Код уже внесён {used:%d.%m.%Y %H:%M} ({row.used_by})")
    if row.status == RedemptionStatus.EXPIRED or ensure_utc(row.expires_at) <= datetime.now(timezone.utc):
        raise RewardError("Срок действия кода истёк")
    if row.status == RedemptionStatus.CANCELLED:
        raise RewardError("Код отменён")

    row.status = RedemptionStatus.SUBMITTED
    row.used_at = datetime.now(timezone.utc)
    row.used_by = admin
    db.add(row)
    db.commit()
    return row


def approve_code(db: Session, code: str, admin: str) -> RewardRedemption:
    """Владелец подтверждает внесённый код. Только после этого строка
    попадает в выгрузку для таблицы компенсаций."""
    row = lookup(db, code)
    if row is None:
        raise RewardError("Код не найден")
    if row.status == RedemptionStatus.APPROVED:
        raise RewardError("Код уже подтверждён")
    if row.status != RedemptionStatus.SUBMITTED:
        raise RewardError("Подтверждать можно только внесённый на стойке код")

    row.status = RedemptionStatus.APPROVED
    row.approved_at = datetime.now(timezone.utc)
    row.approved_by = admin
    db.add(row)
    db.commit()
    return row


def reject_code(db: Session, code: str, admin: str) -> RewardRedemption:
    """Отклонение внесённого кода — возвращает его гостю в активные,
    если срок ещё не вышел, иначе просто отменяет."""
    row = lookup(db, code)
    if row is None:
        raise RewardError("Код не найден")
    if row.status != RedemptionStatus.SUBMITTED:
        raise RewardError("Отклонить можно только внесённый на стойке код")

    still_valid = ensure_utc(row.expires_at) > datetime.now(timezone.utc)
    row.status = RedemptionStatus.PENDING if still_valid else RedemptionStatus.EXPIRED
    row.used_at = None
    row.used_by = None
    db.add(row)
    db.commit()
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
    """Ручное начисление PTS админом (компенсации, акции)."""
    pts.credit(db, user, amount, reason=TxReason.MANUAL, comment=comment)
    achievements.on_pts_changed(db, user)
    db.commit()
