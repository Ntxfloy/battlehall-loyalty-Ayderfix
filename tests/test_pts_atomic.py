"""Регрессии атомарного изменения баланса PTS."""

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import PtsTransaction, TxReason, User
from app.services import pts


def _transactions(db, user_id: int) -> list[PtsTransaction]:
    return list(
        db.execute(
            select(PtsTransaction)
            .where(PtsTransaction.user_id == user_id)
            .order_by(PtsTransaction.id)
        ).scalars()
    )


def test_stale_session_cannot_double_debit(db, user):
    """Устаревший ORM-объект не может повторно потратить уже списанные PTS."""
    pts.credit(db, user, 100, reason=TxReason.TOPUP, comment="старт")
    db.commit()

    first = SessionLocal()
    second = SessionLocal()
    try:
        first_user = first.get(User, user.id)
        second_user = second.get(User, user.id)
        # Закрываем read-транзакции, но сохраняем объекты устаревшими:
        # SessionLocal настроен с expire_on_commit=False.
        first.commit()
        second.commit()

        pts.debit(first, first_user, 80, reason=TxReason.REWARD_REDEEM)
        first.commit()

        with pytest.raises(pts.InsufficientFunds) as exc:
            pts.debit(second, second_user, 80, reason=TxReason.REWARD_REDEEM)
        assert exc.value.needed == 80
        assert exc.value.available == 20
        second.rollback()
    finally:
        first.close()
        second.close()

    with SessionLocal() as verify:
        assert verify.get(User, user.id).pts_balance == 20
        debits = [tx for tx in _transactions(verify, user.id) if tx.amount == -80]
        assert len(debits) == 1
        assert debits[0].balance_after == 20


def test_stale_sessions_do_not_lose_credit(db, user):
    """Два начисления со stale ORM-объектами складываются, а не затирают друг друга."""
    first = SessionLocal()
    second = SessionLocal()
    try:
        first_user = first.get(User, user.id)
        second_user = second.get(User, user.id)
        first.commit()
        second.commit()

        pts.credit(first, first_user, 100, reason=TxReason.TOPUP)
        first.commit()
        pts.credit(second, second_user, 200, reason=TxReason.TOPUP)
        second.commit()
    finally:
        first.close()
        second.close()

    with SessionLocal() as verify:
        assert verify.get(User, user.id).pts_balance == 300
        txs = _transactions(verify, user.id)
        assert [tx.amount for tx in txs] == [100, 200]
        assert [tx.balance_after for tx in txs] == [100, 300]


def test_debit_rollback_restores_balance_and_ledger(db, user):
    """Rollback отменяет и атомарное списание, и запись журнала."""
    pts.credit(db, user, 500, reason=TxReason.TOPUP)
    db.commit()

    pts.debit(db, user, 300, reason=TxReason.REWARD_REDEEM)
    assert user.pts_balance == 200
    db.rollback()

    with SessionLocal() as verify:
        assert verify.get(User, user.id).pts_balance == 500
        assert [tx.amount for tx in _transactions(verify, user.id)] == [500]


def test_credit_rollback_restores_balance_and_ledger(db, user):
    """Rollback отменяет и атомарное начисление, и запись журнала."""
    pts.credit(db, user, 250, reason=TxReason.TOPUP)
    assert user.pts_balance == 250
    db.rollback()

    with SessionLocal() as verify:
        assert verify.get(User, user.id).pts_balance == 0
        assert _transactions(verify, user.id) == []


def test_balance_after_matches_atomic_update(db, user):
    """Каждая запись журнала содержит баланс, полученный атомарным UPDATE."""
    pts.credit(db, user, 1000, reason=TxReason.TOPUP)
    pts.debit(db, user, 300, reason=TxReason.REWARD_REDEEM)
    pts.credit(db, user, 50, reason=TxReason.ACHIEVEMENT)
    db.commit()

    db.refresh(user)
    assert user.pts_balance == 750
    txs = _transactions(db, user.id)
    assert [tx.amount for tx in txs] == [1000, -300, 50]
    assert [tx.balance_after for tx in txs] == [1000, 700, 750]
