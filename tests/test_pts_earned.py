"""Тесты расчёта total_earned: учитываются только заработанные PTS (ACHIEVEMENT, TOPUP).
Возвраты, ручные начисления и выигрыши в колесе не считаются заработком.
"""

from datetime import datetime, timedelta, timezone
import pytest

from app.admin_auth import create_session_value
from app.models import AdminRole, AdminUser, PrizeKind, PrizeRarity, Reward, TxReason, Wheel, WheelPrize
from app.services import achievements, pts, rewards, wheel as wheel_service


def test_total_earned_ignores_refund(db, user):
    """Возврат за сгоревший код не увеличивает total_earned."""
    # 1. Начальное начисление (через ачивку)
    pts.credit(db, user, 3000, reason=TxReason.ACHIEVEMENT, comment="Стартовые PTS")
    db.commit()
    initial_earned = pts.total_earned(db, user.id)
    assert initial_earned == 3000

    # 2. Обмен на награду
    reward = db.query(Reward).filter(Reward.code == "cash_300").one()
    row = rewards.redeem(db, user, reward.id)
    db.commit()

    # 3. Истечение кода с возвратом
    row.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.add(row)
    db.commit()

    expired = rewards.expire_due(db)
    db.commit()
    assert expired == 1

    # total_earned не должен измениться
    assert pts.total_earned(db, user.id) == initial_earned


def test_total_earned_ignores_manual_grant(client, db, user):
    """Ручная компенсация администратора не увеличивает total_earned."""
    owner = AdminUser(
        username="owner_granter",
        password_hash="fakehash",
        display_name="Владелец",
        role=AdminRole.OWNER,
    )
    db.add(owner)
    db.commit()

    token = create_session_value(owner.id)
    client.cookies.set("bh_admin_session", token)

    initial_earned = pts.total_earned(db, user.id)

    resp = client.post(
        f"/api/console/users/{user.telegram_id}/pts",
        json={"amount": 500, "comment": "Компенсация за сбой"},
    )
    assert resp.status_code == 200

    db.refresh(user)
    assert pts.total_earned(db, user.id) == initial_earned
    assert user.pts_balance >= 500

    client.cookies.clear()


def test_total_earned_ignores_wheel_winnings(db, user):
    """Выигрыш PTS в ЛУДЛЕНТЕ не увеличивает total_earned."""
    pts.credit(db, user, 5000, reason=TxReason.ACHIEVEMENT, comment="Стартовый баланс")
    db.commit()
    initial_earned = pts.total_earned(db, user.id)

    # Создаём колесо со 100% шансом выпадения PTS-приза
    wheel = Wheel(code="wheel_test_pts", title="Тест колесо", cost_pts=100, is_active=True)
    db.add(wheel)
    db.flush()

    prize = WheelPrize(
        wheel_id=wheel.id,
        title="1000 PTS",
        kind=PrizeKind.PTS,
        rarity=PrizeRarity.EPIC,
        pts_amount=1000,
        weight=100,
    )
    db.add(prize)
    db.commit()

    initial_balance = user.pts_balance
    res = wheel_service.spin(db, user, wheel.id, count=1)
    db.commit()

    assert res["spins"][0]["prize"]["pts_won"] == 1000
    db.refresh(user)
    # Баланс вырос: -100 (стоимость) + 1000 (выигрыш) = +900
    assert user.pts_balance == initial_balance - 100 + 1000
    # total_earned не изменился
    assert pts.total_earned(db, user.id) == initial_earned


def test_total_earned_counts_achievement_claim(db, user):
    """Забор награды за достижение увеличивает total_earned."""
    initial_earned = pts.total_earned(db, user.id)

    achievements.mark_completed(db, user, "special_channel_sub")
    db.commit()

    claimed_row = achievements.claim(db, user, "special_channel_sub")
    db.commit()

    assert pts.total_earned(db, user.id) == initial_earned + claimed_row.reward_pts


def test_wheel_prize_uses_wheel_reason(db, user):
    """Выигрыш в колесе записывается с reason == TxReason.WHEEL_PRIZE."""
    pts.credit(db, user, 1000, reason=TxReason.TOPUP, comment="Пополнение")
    db.commit()

    wheel = Wheel(code="wheel_pts_reason", title="PTS Wheel", cost_pts=50, is_active=True)
    db.add(wheel)
    db.flush()

    prize = WheelPrize(
        wheel_id=wheel.id,
        title="500 PTS Приз",
        kind=PrizeKind.PTS,
        rarity=PrizeRarity.RARE,
        pts_amount=500,
        weight=100,
    )
    db.add(prize)
    db.commit()

    wheel_service.spin(db, user, wheel.id, count=1)
    db.commit()

    txs = pts.history(db, user.id)
    reasons = [t.reason for t in txs]
    assert TxReason.WHEEL_PRIZE in reasons
    # Ни одной транзакции с ACHIEVEMENT от прокрутки колеса
    wheel_txs = [t for t in txs if t.ref_type == "wheel_prize"]
    assert len(wheel_txs) == 1
    assert wheel_txs[0].reason == TxReason.WHEEL_PRIZE
