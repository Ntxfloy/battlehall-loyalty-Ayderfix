"""Регрессии для открытых runtime-пунктов из Roadmap."""

import pytest
from pydantic import ValidationError

from app.models import PrizeKind, User, Wheel, WheelPrize
from app.schemas import PrizeRequest, RewardCreateRequest, SessionEndPayload, SessionStartPayload
from app.services import pts, referrals
from app.services import wheel as wheel_service


def _new_user(db, telegram_id: int) -> User:
    user = User(
        telegram_id=telegram_id,
        first_name=f"U{telegram_id}",
        referral_code=referrals.generate_code(db),
    )
    db.add(user)
    db.flush()
    return user


def test_spin_returns_persisted_id(db, user):
    wheel = Wheel(code="spin-id", title="ID", cost_pts=10)
    db.add(wheel)
    db.flush()
    db.add(WheelPrize(wheel_id=wheel.id, title="Пусто", kind=PrizeKind.NOTHING, weight=1))
    pts.credit(db, user, 10, comment="тест")
    db.commit()

    result = wheel_service.spin(db, user, wheel.id)

    assert isinstance(result["spins"][0]["spin_id"], int)
    assert result["spins"][0]["spin_id"] > 0


def test_broken_prize_refunds_one_spin(db, user):
    wheel = Wheel(code="broken-prize", title="Битая", cost_pts=70)
    db.add(wheel)
    db.flush()
    db.add(WheelPrize(wheel_id=wheel.id, title="???", kind="typo", weight=1))
    pts.credit(db, user, 100, comment="тест")
    db.commit()

    result = wheel_service.spin(db, user, wheel.id)

    assert result["balance"] == 100
    assert result["spins"][0]["prize"]["kind"] == PrizeKind.NOTHING


def test_zero_cost_wheel_is_rejected_at_runtime(db, user):
    wheel = Wheel(code="broken-free", title="Бесплатная", cost_pts=0)
    db.add(wheel)
    db.flush()
    db.add(WheelPrize(wheel_id=wheel.id, title="Пусто", kind=PrizeKind.NOTHING, weight=1))
    db.commit()

    with pytest.raises(wheel_service.WheelError, match="стоимость"):
        wheel_service.spin(db, user, wheel.id, all_in=True)


def test_referral_cycle_is_rejected(db):
    first = _new_user(db, 700001)
    second = _new_user(db, 700002)
    third = _new_user(db, 700003)
    db.commit()

    assert referrals.attach(db, second, first.referral_code) is True
    assert referrals.attach(db, third, second.referral_code) is True
    assert referrals.attach(db, first, third.referral_code) is False


def test_referral_summary_uses_counts(db):
    inviter = _new_user(db, 710001)
    first = _new_user(db, 710002)
    second = _new_user(db, 710003)
    first.referred_by_id = inviter.id
    first.referral_credited = True
    second.referred_by_id = inviter.id
    db.commit()

    result = referrals.summary(db, inviter)

    assert result["invited_total"] == 2
    assert result["invited_credited"] == 1


def test_session_payload_bounds_and_time_order():
    with pytest.raises(ValidationError):
        SessionStartPayload(session_id="", pc_number=1, started_at="2026-08-23T10:00:00Z")
    with pytest.raises(ValidationError):
        SessionEndPayload(
            session_id="x",
            pc_number=1,
            started_at="2026-08-23T10:00:00Z",
            ended_at="2026-08-23T09:59:00Z",
        )


def test_catalog_enums_and_non_negative_payout():
    with pytest.raises(ValidationError):
        PrizeRequest(title="Ошибка", kind="typo")
    with pytest.raises(ValidationError):
        RewardCreateRequest(code="bad", title="Ошибка", cost_pts=1, payout_value=-1)
    with pytest.raises(ValidationError):
        RewardCreateRequest(code="bad", title="Ошибка", cost_pts=1, payout_unit="roubles")
