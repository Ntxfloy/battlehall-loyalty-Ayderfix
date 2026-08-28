"""Регрессии для оставшихся инвариантов из Roadmap."""

from datetime import datetime, timedelta, timezone

import pytest

from app.models import PrizeKind, RedemptionStatus, Reward, RewardRedemption, Wheel, WheelPrize
from app.schemas import SessionEndPayload, SessionStartPayload
from app.services import achievements, pts, rewards, sessions
from app.services import wheel as wheel_service


def _reward_wheel(db, reward: Reward) -> Wheel:
    wheel = Wheel(code="reward_invariant", title="Лента с кодом", cost_pts=100)
    db.add(wheel)
    db.flush()
    db.add(
        WheelPrize(
            wheel_id=wheel.id,
            title=reward.title,
            kind=PrizeKind.REWARD,
            reward_id=reward.id,
            weight=1,
        )
    )
    db.commit()
    return wheel


def test_reward_wheel_rejects_batch_before_debit(db, user):
    reward = db.query(Reward).filter(Reward.code == "cash_300").one()
    wheel = _reward_wheel(db, reward)
    pts.credit(db, user, 1_000, comment="тест")
    db.commit()

    with pytest.raises(wheel_service.WheelError, match="только одна прокрутка"):
        wheel_service.spin(db, user, wheel.id, count=5)

    db.refresh(user)
    assert user.pts_balance == 1_000
    assert wheel_service.history(db, user.id) == []
    wheel_codes = db.query(RewardRedemption).filter(
        RewardRedemption.user_id == user.id,
        RewardRedemption.source == "wheel",
    ).all()
    assert wheel_codes == []


def test_reward_wheel_rejects_spin_while_active_code_exists(db, user):
    reward = db.query(Reward).filter(Reward.code == "cash_300").one()
    wheel = _reward_wheel(db, reward)
    pts.credit(db, user, 1_000, comment="тест")
    rewards.issue_code(db, user, reward)
    db.commit()

    with pytest.raises(wheel_service.WheelError, match="уже есть активный код"):
        wheel_service.spin(db, user, wheel.id)

    db.refresh(user)
    assert user.pts_balance == 1_000
    pending = db.query(RewardRedemption).filter(
        RewardRedemption.user_id == user.id,
        RewardRedemption.status == RedemptionStatus.PENDING,
    ).all()
    assert len(pending) == 1
    assert wheel_service.history(db, user.id) == []


def _minute_progress(db, user) -> dict[str, int]:
    overview = achievements.overview(db, user)
    return {
        item["code"]: item["progress"]
        for items in overview.values()
        for item in items
        if item["unit"] == "мин"
    }


def test_duplicate_session_end_does_not_count_minutes_twice(db, user, club):
    started_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    start = SessionStartPayload(
        session_id="duplicate-end",
        pc_number=15,
        started_at=started_at,
        telegram_id=user.telegram_id,
    )
    end = SessionEndPayload(
        session_id=start.session_id,
        pc_number=start.pc_number,
        started_at=started_at,
        ended_at=started_at + timedelta(minutes=30),
        duration_minutes=30,
        telegram_id=user.telegram_id,
    )

    sessions.start_session(db, club, start)
    row, closed = sessions.end_session(db, club, end)
    db.commit()
    first = _minute_progress(db, user)

    same_row, closed_again = sessions.end_session(db, club, end)
    db.commit()
    second = _minute_progress(db, user)

    assert closed is True
    assert closed_again is False
    assert same_row.id == row.id
    assert first
    assert second == first
