"""Движение PTS. Любое начисление и списание проходит только через этот модуль,
чтобы баланс и журнал никогда не разъезжались."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import PtsTransaction, TxReason, User


class InsufficientFunds(Exception):
    def __init__(self, needed: int, available: int) -> None:
        super().__init__(f"нужно {needed} PTS, на балансе {available}")
        self.needed = needed
        self.available = available


def _write(
    db: Session,
    user: User,
    amount: int,
    reason: str,
    ref_type: str | None = None,
    ref_id: str | None = None,
    comment: str | None = None,
) -> PtsTransaction:
    user.pts_balance += amount
    tx = PtsTransaction(
        user_id=user.id,
        amount=amount,
        balance_after=user.pts_balance,
        reason=reason,
        ref_type=ref_type,
        ref_id=ref_id,
        comment=comment,
    )
    db.add(tx)
    db.add(user)
    db.flush()
    return tx


def credit(
    db: Session,
    user: User,
    amount: int,
    reason: str = TxReason.MANUAL,
    ref_type: str | None = None,
    ref_id: str | None = None,
    comment: str | None = None,
) -> PtsTransaction:
    if amount < 0:
        raise ValueError("credit ждёт положительную сумму")
    return _write(db, user, amount, reason, ref_type, ref_id, comment)


def debit(
    db: Session,
    user: User,
    amount: int,
    reason: str = TxReason.MANUAL,
    ref_type: str | None = None,
    ref_id: str | None = None,
    comment: str | None = None,
) -> PtsTransaction:
    if amount < 0:
        raise ValueError("debit ждёт положительную сумму")
    if user.pts_balance < amount:
        raise InsufficientFunds(amount, user.pts_balance)
    return _write(db, user, -amount, reason, ref_type, ref_id, comment)


def total_earned(db: Session, user_id: int) -> int:
    """Сколько PTS пользователь заработал за всё время — для накопительной ачивки.
    Считаем только начисления, списания на неё не влияют."""
    total = db.execute(
        select(func.coalesce(func.sum(PtsTransaction.amount), 0)).where(
            PtsTransaction.user_id == user_id,
            PtsTransaction.amount > 0,
        )
    ).scalar_one()
    return int(total)


def history(db: Session, user_id: int, limit: int = 50) -> list[PtsTransaction]:
    return list(
        db.execute(
            select(PtsTransaction)
            .where(PtsTransaction.user_id == user_id)
            .order_by(PtsTransaction.created_at.desc(), PtsTransaction.id.desc())
            .limit(limit)
        ).scalars()
    )
