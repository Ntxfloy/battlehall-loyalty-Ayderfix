from datetime import datetime, timedelta, timezone

import pytest

from app.models import RedemptionStatus, Reward, TxReason, User
from app.schemas import SessionEndPayload, SessionStartPayload
from app.services import achievements, pts, referrals, rewards, sessions



def _start(db, club, user, pc, when, session_id, minutes=None):
    payload = SessionStartPayload(
        session_id=session_id,
        pc_number=pc,
        started_at=when,
        telegram_id=user.telegram_id,
    )
    row, created = sessions.start_session(db, club, payload)
    if minutes is not None:
        sessions.end_session(
            db,
            club,
            SessionEndPayload(
                session_id=session_id,
                pc_number=pc,
                started_at=when,
                telegram_id=user.telegram_id,
                ended_at=when + timedelta(minutes=minutes),
                duration_minutes=minutes,
            ),
        )
    return row, created


def _item(db, user, code):
    overview = achievements.overview(db, user)
    for items in overview.values():
        for item in items:
            if item["code"] == code:
                return item
    raise AssertionError(f"ачивка {code} не найдена")


# --- приём сессий ---

def test_session_ingest_is_idempotent(db, club, user):
    now = datetime.now(timezone.utc)
    _, created_first = _start(db, club, user, 20, now, "s-1")
    _, created_second = _start(db, club, user, 20, now, "s-1")
    assert created_first is True
    assert created_second is False


def test_unlinked_guest_is_skipped(db, club):
    payload = SessionStartPayload(
        session_id="s-unknown",
        pc_number=20,
        started_at=datetime.now(timezone.utc),
        phone="+7 999 000-00-00",
    )
    with pytest.raises(sessions.UserNotLinked):
        sessions.start_session(db, club, payload)


def test_unknown_pc_is_rejected(db, club, user):
    payload = SessionStartPayload(
        session_id="s-bad-pc",
        pc_number=49,
        started_at=datetime.now(timezone.utc),
        telegram_id=user.telegram_id,
    )
    payload.pc_number = 99  # обходим валидацию pydantic, проверяем защиту сервиса
    with pytest.raises(sessions.SessionIngestError):
        sessions.start_session(db, club, payload)


def test_end_without_start_restores_session(db, club, user):
    now = datetime.now(timezone.utc)
    row, _ = sessions.end_session(
        db,
        club,
        SessionEndPayload(
            session_id="s-lost",
            pc_number=12,
            started_at=now - timedelta(minutes=90),
            telegram_id=user.telegram_id,
            ended_at=now,
        ),
    )
    assert row.is_closed
    assert row.duration_minutes == 90
    assert row.zone_code == "VIP_A"


# --- достижения ---

def test_daily_checkin_completes_and_pays_once(db, club, user):
    _start(db, club, user, 20, datetime.now(timezone.utc), "s-checkin")

    item = _item(db, user, "daily_checkin")
    assert item["is_completed"] is True
    assert item["can_claim"] is True

    achievements.claim(db, user, "daily_checkin")
    db.commit()
    assert user.pts_balance == 50

    with pytest.raises(achievements.AchievementError):
        achievements.claim(db, user, "daily_checkin")


def test_claim_requires_completion(db, user):
    with pytest.raises(achievements.AchievementError):
        achievements.claim(db, user, "month_days_15")


def test_all_zones_achievement(db, club, user):
    now = datetime.now(timezone.utc)
    for index, pc in enumerate((20, 10, 1, 6, 3)):   # STANDARD, VIP, DUO, TRIO, SOLO
        _start(db, club, user, pc, now + timedelta(minutes=index), f"s-zone-{pc}")

    item = _item(db, user, "month_all_zones")
    assert item["progress"] == 5
    assert item["is_completed"] is True


def test_same_zone_twice_counts_once(db, club, user):
    now = datetime.now(timezone.utc)
    _start(db, club, user, 20, now, "s-z1")
    _start(db, club, user, 21, now + timedelta(minutes=5), "s-z2")   # тот же СТАНДАРТ ЗАЛ

    assert _item(db, user, "month_all_zones")["progress"] == 1


def test_minutes_accumulate_on_session_end(db, club, user):
    now = datetime.now(timezone.utc)
    _start(db, club, user, 20, now, "s-h1", minutes=300)
    _start(db, club, user, 20, now + timedelta(hours=6), "s-h2", minutes=300)

    item = _item(db, user, "week_hours_10")
    assert item["progress"] == 600
    assert item["is_completed"] is True


def test_progress_is_capped_at_target(db, club, user):
    _start(db, club, user, 20, datetime.now(timezone.utc), "s-cap", minutes=1000)
    assert _item(db, user, "week_hours_10")["progress"] == 600


def test_night_session_counts_to_previous_game_day(db, club, user):
    """Две сессии в календарно разные сутки, но в один игровой день,
    дают одну отметку в ачивке «разные дни недели»."""
    evening = datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc)
    after_midnight = datetime(2026, 8, 20, 22, 30, tzinfo=timezone.utc)  # 01:30 МСК 21-го

    _start(db, club, user, 20, evening, "s-n1")
    _start(db, club, user, 20, after_midnight, "s-n2")

    assert _item(db, user, "week_weekdays_3")["progress"] == 1


def test_cumulative_pts_achievement_tracks_earnings(db, user):
    pts.credit(db, user, 5000, reason=TxReason.ACHIEVEMENT, comment="тест")
    achievements.on_pts_changed(db, user)
    db.commit()


    item = _item(db, user, "special_pts_5000")
    assert item["is_completed"] is True


# --- награды ---

def _cheap_reward(db):
    reward = db.query(Reward).filter(Reward.code == "cash_300").one()
    return reward


def test_redeem_spends_pts_and_issues_code(db, user):
    pts.credit(db, user, 5000, comment="тест")
    db.commit()

    reward = _cheap_reward(db)
    redemption = rewards.redeem(db, user, reward.id)

    assert len(redemption.code) == 8
    assert redemption.status == RedemptionStatus.PENDING
    assert user.pts_balance == 5000 - reward.cost_pts


def test_second_active_code_is_blocked(db, user):
    pts.credit(db, user, 20000, comment="тест")
    db.commit()
    reward = _cheap_reward(db)
    rewards.redeem(db, user, reward.id)

    with pytest.raises(rewards.RewardError):
        rewards.redeem(db, user, reward.id)


def test_not_enough_pts(db, user):
    with pytest.raises(rewards.RewardError):
        rewards.redeem(db, user, _cheap_reward(db).id)


def test_code_can_be_used_once(db, user):
    pts.credit(db, user, 5000, comment="тест")
    db.commit()
    redemption = rewards.redeem(db, user, _cheap_reward(db).id)

    rewards.use_code(db, redemption.code.lower(), "admin")   # регистр не важен
    with pytest.raises(rewards.RewardError):
        rewards.use_code(db, redemption.code, "admin")


def test_expired_code_refunds_pts(db, user):
    pts.credit(db, user, 5000, comment="тест")
    db.commit()
    reward = _cheap_reward(db)
    redemption = rewards.redeem(db, user, reward.id)

    redemption.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.add(redemption)
    db.commit()

    assert rewards.expire_due(db) == 1
    db.refresh(redemption)
    db.refresh(user)
    assert redemption.status == RedemptionStatus.EXPIRED
    assert user.pts_balance == 5000     # PTS вернулись


# --- рефералы ---

def test_referral_credits_after_required_playtime(db, club, user):
    friend = User(telegram_id=555002, first_name="Друг", referral_code=referrals.generate_code(db))
    db.add(friend)
    db.commit()

    assert referrals.attach(db, friend, user.referral_code) is True

    now = datetime.now(timezone.utc)
    _start(db, club, friend, 20, now, "s-ref-short", minutes=30)
    assert _item(db, user, "special_referral")["is_completed"] is False

    _start(db, club, friend, 20, now + timedelta(hours=2), "s-ref-long", minutes=45)
    db.refresh(friend)
    assert friend.referral_credited is True
    assert _item(db, user, "special_referral")["is_completed"] is True


def test_cannot_refer_yourself(db, user):
    assert referrals.attach(db, user, user.referral_code) is False


def test_referral_link_ignored_for_existing_player(db, club, user):
    veteran = User(telegram_id=555003, first_name="Ветеран", referral_code=referrals.generate_code(db))
    db.add(veteran)
    db.commit()
    _start(db, club, veteran, 20, datetime.now(timezone.utc), "s-vet", minutes=120)

    assert referrals.attach(db, veteran, user.referral_code) is False


def test_end_session_survives_naive_dates_from_db(db, club, user):
    """Регрессия: SQLite отдаёт даты без таймзоны, и расчёт длительности
    падал на вычитании naive и aware."""
    now = datetime.now(timezone.utc)
    _start(db, club, user, 20, now, "s-naive")
    db.expire_all()   # как в отдельном HTTP-запросе: объект перечитывается из базы

    row, closed = sessions.end_session(
        db,
        club,
        SessionEndPayload(
            session_id="s-naive",
            pc_number=20,
            started_at=now,
            telegram_id=user.telegram_id,
            ended_at=now + timedelta(minutes=90),
        ),
    )
    assert closed is True
    assert row.duration_minutes == 90
