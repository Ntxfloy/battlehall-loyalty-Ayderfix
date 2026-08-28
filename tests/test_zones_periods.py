from datetime import datetime, timedelta, timezone

from app.periods import MSK, game_day, month_key, period_ends_at, week_key
from app.zones import PC_TO_ZONE, TOTAL_PCS, ZONE_TYPES, zone_for_pc


def test_all_pcs_mapped_without_gaps():
    """49 машин расписаны по зонам без дыр и пересечений."""
    assert set(PC_TO_ZONE) == set(range(1, TOTAL_PCS + 1))


def test_zone_boundaries():
    assert zone_for_pc(1).code == "DUO_A"
    assert zone_for_pc(3).code == "SOLO"
    assert zone_for_pc(9).code == "SOLO"
    assert zone_for_pc(10).code == "VIP_A"
    assert zone_for_pc(14).code == "VIP_A"
    assert zone_for_pc(15).code == "STANDARD_HALL"
    assert zone_for_pc(44).code == "STANDARD_HALL"
    assert zone_for_pc(45).code == "VIP_B"
    assert zone_for_pc(49).code == "VIP_B"
    assert zone_for_pc(50) is None


def test_five_zone_types():
    """Ачивка «все зоны клуба» рассчитана ровно на 5 типов."""
    assert len(ZONE_TYPES) == 5
    assert {z.zone_type for z in map(zone_for_pc, range(1, TOTAL_PCS + 1))} == set(ZONE_TYPES)


def test_night_session_belongs_to_previous_game_day():
    """03:00 МСК — это ещё вчерашний игровой день."""
    night = datetime(2026, 8, 20, 3, 0, tzinfo=MSK)
    assert game_day(night).isoformat() == "2026-08-19"


def test_game_day_flips_at_six_msk():
    assert game_day(datetime(2026, 8, 20, 5, 59, tzinfo=MSK)).isoformat() == "2026-08-19"
    assert game_day(datetime(2026, 8, 20, 6, 0, tzinfo=MSK)).isoformat() == "2026-08-20"


def test_utc_input_is_converted_to_msk():
    """Вебхуки приходят в UTC — 23:30 UTC это уже 02:30 МСК следующих суток."""
    assert game_day(datetime(2026, 8, 19, 23, 30, tzinfo=timezone.utc)).isoformat() == "2026-08-19"


def test_week_resets_monday_morning():
    sunday_night = datetime(2026, 8, 24, 5, 0, tzinfo=MSK)   # понедельник до 06:00
    monday_day = datetime(2026, 8, 24, 7, 0, tzinfo=MSK)
    assert week_key(sunday_night) != week_key(monday_day)


def test_month_key_uses_game_day():
    assert month_key(datetime(2026, 9, 1, 4, 0, tzinfo=MSK)) == "2026-08"
    assert month_key(datetime(2026, 9, 1, 8, 0, tzinfo=MSK)) == "2026-09"


def test_period_ends_at_is_in_the_future():
    now = datetime.now(timezone.utc)
    for period in ("day", "week", "month"):
        ends = period_ends_at(period, now)
        assert ends > now
        assert ends - now < timedelta(days=32)
