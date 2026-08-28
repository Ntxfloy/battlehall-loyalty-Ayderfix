"""Деньги не двоятся.

Проверяем главный инвариант кошелька: одна бизнес-операция изменяет баланс
ровно один раз, сколько бы раз её ни повторили клиент или регламент.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models import (
    AchievementDef,
    PrizeKind,
    PtsTransaction,
    RedemptionStatus,
    Reward,
    RewardRedemption,
    TxReason,
    Wheel,
    WheelPrize,
)
from app.services import achievements, pts, rewards
from app.services import wheel as wheel_service


def _balance(db, user) -> int:
    """Баланс из базы, а не из кэша сессии: движение денег идёт через UPDATE."""
    db.expire(user, ["pts_balance"])
    return user.pts_balance


def _tx_count(db, user) -> int:
    return int(
        db.execute(
            select(func.count(PtsTransaction.id)).where(PtsTransaction.user_id == user.id)
        ).scalar_one()
    )


def test_credit_with_same_key_applies_once(db, user):
    first = pts.credit(db, user, 100, reason=TxReason.MANUAL, idem_key="promo:1")
    second = pts.credit(db, user, 100, reason=TxReason.MANUAL, idem_key="promo:1")

    assert first.id == second.id
    assert _balance(db, user) == 100
    assert _tx_count(db, user) == 1


def test_debit_with_same_key_applies_once(db, user):
    pts.credit(db, user, 500, reason=TxReason.MANUAL)

    first = pts.debit(db, user, 120, reason=TxReason.MANUAL, idem_key="order:42")
    second = pts.debit(db, user, 120, reason=TxReason.MANUAL, idem_key="order:42")

    assert first.id == second.id
    assert _balance(db, user) == 380


def test_different_keys_apply_separately(db, user):
    pts.credit(db, user, 10, reason=TxReason.MANUAL, idem_key="a")
    pts.credit(db, user, 10, reason=TxReason.MANUAL, idem_key="b")

    assert _balance(db, user) == 20
    assert _tx_count(db, user) == 2


def test_calls_without_key_are_not_deduplicated(db, user):
    """Старое поведение сохранено: без ключа каждый вызов — отдельная операция."""
    pts.credit(db, user, 25, reason=TxReason.MANUAL)
    pts.credit(db, user, 25, reason=TxReason.MANUAL)

    assert _balance(db, user) == 50
    assert _tx_count(db, user) == 2


def _make_achievement(db, code: str, reward_pts: int) -> AchievementDef:
    adef = AchievementDef(
        code=code,
        title=f"Тест {code}",
        description="",
        category="special",
        period="day",
        target=1,
        reward_pts=reward_pts,
        unit="раз",
        sort_order=0,
        is_active=True,
        is_implemented=True,
    )
    db.add(adef)
    db.flush()
    return adef


def test_claim_with_zero_reward_does_not_crash(db, user):
    """Имиджевая ачивка без PTS раньше роняла claim через ValueError в credit."""
    _make_achievement(db, "test_zero_reward", reward_pts=0)
    achievements.mark_completed(db, user, "test_zero_reward")

    row = achievements.claim(db, user, "test_zero_reward")

    assert row.claimed_at is not None
    assert _balance(db, user) == 0
    assert _tx_count(db, user) == 0


def test_claim_credits_once_even_if_flag_reset(db, user):
    _make_achievement(db, "test_reward_50", reward_pts=50)
    achievements.mark_completed(db, user, "test_reward_50")

    row = achievements.claim(db, user, "test_reward_50")
    assert _balance(db, user) == 50

    # Имитируем аварию: отметка «забрано» потеряна, гость жмёт кнопку снова.
    row.claimed_at = None
    db.add(row)
    db.flush()

    achievements.claim(db, user, "test_reward_50")

    assert _balance(db, user) == 50   # второго начисления не было


def _make_reward(db, cost: int = 100) -> Reward:
    reward = Reward(
        code=f"test_reward_{cost}",
        kind="cash",
        title="Тестовая награда",
        description="",
        cost_pts=cost,
        payout_value=100,
        payout_unit="RUB",
        sort_order=0,
        is_active=True,
    )
    db.add(reward)
    db.flush()
    return reward


def test_issue_code_retries_on_collision(db, user, monkeypatch):
    """Гонка двух процессов не должна ломать выдачу кода."""
    reward = _make_reward(db)

    monkeypatch.setattr(rewards, "_random_code", lambda: "DUPLICAT")
    first = rewards.issue_code(db, user, reward)
    assert first.code == "DUPLICAT"

    # Генератор сначала отдаёт занятый код, а проверка «свободен?» врёт —
    # ровно так выглядит конкурентная вставка из другого процесса.
    candidates = iter(["DUPLICAT", "UNIQUECD"])
    monkeypatch.setattr(rewards, "_random_code", lambda: next(candidates))
    monkeypatch.setattr(rewards, "_code_taken", lambda _db, _code: False)

    second = rewards.issue_code(db, user, reward)

    assert second.code == "UNIQUECD"
    assert second.id != first.id


def test_expired_code_refunds_pts_once(db, user):
    reward = _make_reward(db, cost=100)
    pts.credit(db, user, 300, reason=TxReason.MANUAL)

    redemption = rewards.redeem(db, user, reward.id)
    assert _balance(db, user) == 200

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    redemption.expires_at = past
    db.add(redemption)
    db.flush()

    assert rewards.expire_due(db) == 1
    assert _balance(db, user) == 300

    # Повторный прогон регламента после ручного сброса отметок.
    redemption.status = RedemptionStatus.PENDING
    redemption.refunded_at = None
    redemption.expired_at = None
    db.add(redemption)
    db.flush()

    rewards.expire_due(db)

    assert _balance(db, user) == 300   # второго возврата не было


def _make_wheel(db, cost: int = 10) -> Wheel:
    wheel = Wheel(
        code="test_wheel",
        title="Тестовая лента",
        description="",
        cost_pts=cost,
        sort_order=0,
        is_active=True,
    )
    db.add(wheel)
    db.flush()
    db.add(
        WheelPrize(
            wheel_id=wheel.id,
            title="+5 PTS",
            kind=PrizeKind.PTS,
            rarity="common",
            pts_amount=5,
            weight=1,
            sort_order=0,
            is_active=True,
        )
    )
    db.flush()
    return wheel


def test_wheel_spin_with_same_key_is_rejected(db, user):
    wheel = _make_wheel(db, cost=10)
    pts.credit(db, user, 100, reason=TxReason.MANUAL)

    wheel_service.spin(db, user, wheel.id, count=1, idem_key="tap-1")
    after_first = _balance(db, user)

    with pytest.raises(wheel_service.WheelError):
        wheel_service.spin(db, user, wheel.id, count=1, idem_key="tap-1")

    assert _balance(db, user) == after_first   # повтор не списал ставку


def test_wheel_spin_with_new_key_works(db, user):
    wheel = _make_wheel(db, cost=10)
    pts.credit(db, user, 100, reason=TxReason.MANUAL)

    wheel_service.spin(db, user, wheel.id, count=1, idem_key="tap-1")
    balance_after_first = _balance(db, user)

    wheel_service.spin(db, user, wheel.id, count=1, idem_key="tap-2")

    assert _balance(db, user) != balance_after_first or balance_after_first == 0
    spins = wheel_service.history(db, user.id)
    assert len(spins) == 2
