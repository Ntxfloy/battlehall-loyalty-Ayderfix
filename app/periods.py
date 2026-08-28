"""Границы игрового дня, недели и месяца.

Игровой день начинается в 06:00 МСК: сессия, начатая в 03:00 в ночь с пятницы
на субботу, относится к пятнице. Из этого следуют все периоды: неделя
обнуляется в понедельник в 06:00 МСК, месяц — в первое число в 06:00 МСК.
"""

from datetime import date, datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))


def to_msk(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MSK)


def game_day(dt: datetime, start_hour: int = 6) -> date:
    """Игровой день, которому принадлежит момент времени."""
    return (to_msk(dt) - timedelta(hours=start_hour)).date()


def day_key(dt: datetime, start_hour: int = 6) -> str:
    return game_day(dt, start_hour).isoformat()


def week_key(dt: datetime, start_hour: int = 6) -> str:
    d = game_day(dt, start_hour)
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def month_key(dt: datetime, start_hour: int = 6) -> str:
    d = game_day(dt, start_hour)
    return f"{d.year}-{d.month:02d}"


def year_key(dt: datetime, start_hour: int = 6) -> str:
    return str(game_day(dt, start_hour).year)


ALL_TIME_KEY = "all"


def period_key(period: str, dt: datetime, start_hour: int = 6) -> str:
    """period: day | week | month | year | all"""
    match period:
        case "day":
            return day_key(dt, start_hour)
        case "week":
            return week_key(dt, start_hour)
        case "month":
            return month_key(dt, start_hour)
        case "year":
            return year_key(dt, start_hour)
        case "all":
            return ALL_TIME_KEY
    raise ValueError(f"unknown period: {period}")


def period_ends_at(period: str, dt: datetime, start_hour: int = 6) -> datetime | None:
    """Момент обнуления периода — чтобы показать в интерфейсе «сгорит через N»."""
    d = game_day(dt, start_hour)
    if period == "day":
        end = d + timedelta(days=1)
    elif period == "week":
        end = d + timedelta(days=7 - d.isoweekday() + 1)
    elif period == "month":
        end = date(d.year + (d.month == 12), (d.month % 12) + 1, 1)
    elif period == "year":
        end = date(d.year + 1, 1, 1)
    else:
        return None
    return datetime.combine(end, datetime.min.time(), tzinfo=MSK) + timedelta(hours=start_hour)


def ensure_utc(dt: datetime) -> datetime:
    """SQLite не хранит таймзону и отдаёт naive datetime. Считаем такие значения
    временем в UTC — в базу мы кладём только UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def iso(dt: datetime | None) -> str | None:
    """ISO-строка со смещением. Без него JS распарсит время как локальное."""
    return ensure_utc(dt).isoformat() if dt is not None else None
