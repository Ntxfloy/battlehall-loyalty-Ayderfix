"""Движение PTS. Любое начисление и списание проходит только через этот модуль,
чтобы баланс и журнал никогда не разъезжались."""

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import PtsTransaction, TxReason, User


class InsufficientFunds(Exception):
    def __init__(self, needed: int, available: int) -> None:
        super().__init__(f"нужно {needed} PTS, на балансе {available}")
        self.needed = needed
        self.available = available


def _change_balance(
    db: Session,
    user: User,
    amount: int,
    reason: str,
    ref_type: str | None = None,
    ref_id: str | None = None,
    comment: str | None = None,
) -> PtsTransaction:
    """Атомарно меняет баланс и добавляет запись в журнал в той же транзакции.

    Баланс вычисляет база, а не загруженный ORM-объект. Поэтому устаревший
    экземпляр User не может привести к двойному списанию или потерянному
    начислению при конкурентных запросах.
    """
    if user.id is None:
        raise ValueError("Пользователь должен быть сохранён до изменения баланса")
    if amount == 0:
        raise ValueError("Изменение баланса не может быть нулевым")

    stmt = update(User).where(User.id == user.id)
    if amount < 0:
        stmt = stmt.where(User.pts_balance >= -amount)

    new_balance = db.execute(
        stmt
        .values(pts_balance=User.pts_balance + amount)
        .returning(User.pts_balance)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()

    if new_balance is None:
        available = db.execute(
            select(User.pts_balance).where(User.id == user.id)
        ).scalar_one_or_none()
        if available is None:
            raise ValueError("Пользователь не найден")
        if amount < 0:
            raise InsufficientFunds(-amount, int(available))
        raise RuntimeError("Не удалось изменить баланс пользователя")

    tx = PtsTransaction(
        user_id=user.id,
        amount=amount,
        balance_after=int(new_balance),
        reason=reason,
        ref_type=ref_type,
        ref_id=ref_id,
        comment=comment,
    )
    db.add(tx)
    db.flush()

    # UPDATE выполнен напрямую. Не присваиваем значение ORM-атрибуту, иначе
    # следующий flush может записать устаревший баланс поверх результата SQL.
    db.expire(user, ["pts_balance"])
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
    if amount <= 0:
        raise ValueError("credit ждёт положительную ненулевую сумму")
    return _change_balance(db, user, amount, reason, ref_type, ref_id, comment)


def debit(
    db: Session,
    user: User,
    amount: int,
    reason: str = TxReason.MANUAL,
    ref_type: str | None = None,
    ref_id: str | None = None,
    comment: str | None = None,
) -> PtsTransaction:
    if amount <= 0:
        raise ValueError("debit ждёт положительную ненулевую сумму")
    return _change_balance(db, user, -amount, reason, ref_type, ref_id, comment)


# Заработком считаем только то, что гость получил за активность в клубе.
# Возврат за сгоревший код, ручная компенсация и выигрыш в «ЛУДЛЕНТЕ» —
# это перекладывание уже начисленных PTS, а не новый заработок.
EARNED_REASONS = frozenset({TxReason.ACHIEVEMENT, TxReason.TOPUP})


def total_earned(db: Session, user_id: int) -> int:
    """Сколько PTS пользователь заработал за всё время — для накопительной ачивки.
    Считаем только начисления из белого списка EARNED_REASONS."""
    total = db.execute(
        select(func.coalesce(func.sum(PtsTransaction.amount), 0)).where(
            PtsTransaction.user_id == user_id,
            PtsTransaction.amount > 0,
            PtsTransaction.reason.in_(EARNED_REASONS),
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
