"""Регрессии оставшихся критикалов аудита: рефералы, reject-refund, link-phone."""

from datetime import datetime, timedelta, timezone

from app.models import AchievementDef, AchievementProgress, RedemptionStatus, Reward, TxReason, User
from app.schemas import SessionEndPayload, SessionStartPayload
from app.services import pts, referrals, rewards, sessions


def test_referral_increments_instead_of_completing(db, user, club):
    adef = db.query(AchievementDef).filter(AchievementDef.code == "special_referral").one()
    adef.target = 3
    db.add(adef)
    db.commit()

    friend = User(
        telegram_id=777001,
        first_name="Друг",
        referral_code=referrals.generate_code(db),
        referred_by_id=user.id,
    )
    db.add(friend)
    db.commit()

    sessions.start_session(
        db,
        club,
        SessionStartPayload(
            session_id="ref-1",
            pc_number=15,
            started_at=datetime.now(timezone.utc),
            telegram_id=friend.telegram_id,
        ),
    )
    sessions.end_session(
        db,
        club,
        SessionEndPayload(
            session_id="ref-1",
            pc_number=15,
            started_at=datetime.now(timezone.utc),
            telegram_id=friend.telegram_id,
            duration_minutes=60,
        ),
    )
    db.commit()

    row = db.query(AchievementProgress).filter(
        AchievementProgress.user_id == user.id,
        AchievementProgress.achievement_code == "special_referral",
    ).one()
    assert row.progress == 1
    assert row.completed_at is None


def test_missing_referral_achievement_does_not_break_session_end(db, user, club):
    db.query(AchievementDef).filter(AchievementDef.code == "special_referral").delete()
    db.commit()

    friend = User(
        telegram_id=777002,
        first_name="Друг2",
        referral_code=referrals.generate_code(db),
        referred_by_id=user.id,
    )
    db.add(friend)
    db.commit()

    sessions.start_session(
        db,
        club,
        SessionStartPayload(
            session_id="ref-miss",
            pc_number=15,
            started_at=datetime.now(timezone.utc),
            telegram_id=friend.telegram_id,
        ),
    )
    _row, closed = sessions.end_session(
        db,
        club,
        SessionEndPayload(
            session_id="ref-miss",
            pc_number=15,
            started_at=datetime.now(timezone.utc),
            telegram_id=friend.telegram_id,
            duration_minutes=60,
        ),
    )
    db.commit()
    assert closed is True
    db.refresh(friend)
    assert friend.referral_credited is True


def test_reject_expired_submitted_code_refunds_pts(db, user):
    pts.credit(db, user, 5000, comment="тест")
    db.commit()
    reward = db.query(Reward).filter(Reward.code == "cash_300").one()
    row = rewards.redeem(db, user, reward.id)
    db.commit()
    rewards.use_code(db, row.code, "desk1")
    db.commit()

    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.add(row)
    db.commit()
    balance_before = user.pts_balance

    rejected = rewards.reject_code(db, row.code, "owner1")
    db.commit()
    assert rejected.status == RedemptionStatus.EXPIRED
    db.refresh(user)
    assert user.pts_balance == balance_before + reward.cost_pts
    refunds = [t for t in pts.history(db, user.id) if t.reason == TxReason.REWARD_REFUND]
    assert len(refunds) == 1


def test_link_phone_does_not_steal_existing_number(client, db, club, user):
    user.phone = "79991112233"
    db.add(user)
    db.commit()
    club.oasys_webhook_token = "valid_secure_webhook_token_32_chars_long"
    db.add(club)
    db.commit()

    resp = client.post(
        f"/api/webhooks/oasys/{club.slug}/link-phone",
        headers={"X-OASys-Token": club.oasys_webhook_token},
        json={"telegram_id": 888001, "phone": "79991112233"},
    )
    assert resp.status_code == 409
