"""Каталог достижений и правила их подсчёта.

Здесь — источник правды при первом развёртывании: `seed.py` заливает этот
список в таблицу `achievement_defs`, дальше названия, цели и награды правятся
из админки без релиза. Меняется только `rule` — она живёт в коде, потому что
это логика, а не настройка.

`is_implemented=False` значит: ачивка описана и видна в каталоге, но её счётчик
ещё не подключён — нет источника данных (Эвотор, вебхук покупок/брони).
"""

from dataclasses import dataclass

from app.models import AchievementCategory as Cat

# --- правила подсчёта ---
# Их обрабатывает движок в app/services/achievements.py
RULE_DAILY_CHECKIN = "daily_checkin"              # факт старта сессии в игровой день
RULE_DISTINCT_DAYS = "distinct_days"              # сколько разных игровых дней в периоде
RULE_DISTINCT_WEEKDAYS = "distinct_weekdays"      # сколько разных дней недели в периоде
RULE_DISTINCT_ZONE_TYPES = "distinct_zone_types"  # сколько разных типов зон в периоде
RULE_MINUTES = "minutes"                          # суммарные минуты игры в периоде
RULE_SESSIONS = "sessions"                        # число закрытых сессий в периоде
RULE_REFERRAL = "referral"                        # друг пришёл по ссылке и отыграл минимум
RULE_CHANNEL_SUB = "channel_sub"                  # подписка на телеграм-канал
RULE_PTS_EARNED = "pts_earned"                    # накопительно все заработанные PTS
RULE_EXTERNAL = "external"                        # счётчик ещё не подключён

# Категория для ежедневной награды: показывается на Главной, а не во вкладке ачивок
CAT_DAILY = "daily"

# Единицы измерения прогресса — фронт по ним форматирует подпись
UNIT_TIMES = "раз"
UNIT_DAYS = "дн."
UNIT_ZONES = "зон"
UNIT_MINUTES = "мин"
UNIT_PTS = "PTS"


@dataclass(frozen=True)
class AchievementSpec:
    code: str
    title: str
    description: str
    category: str
    period: str          # day | week | month | year | all
    target: int
    reward_pts: int
    rule: str
    unit: str = UNIT_TIMES
    sort_order: int = 0
    is_implemented: bool = True


ACHIEVEMENTS: tuple[AchievementSpec, ...] = (
    # --- Ежедневное (Главная) ---
    AchievementSpec(
        code="daily_checkin",
        title="Ежедневная награда",
        description="Начни игровую сессию в клубе и забери награду за день.",
        category=CAT_DAILY,
        period="day",
        target=1,
        reward_pts=50,
        rule=RULE_DAILY_CHECKIN,
        sort_order=0,
    ),

    # --- Еженедельные ---
    AchievementSpec(
        code="week_weekdays_3",
        title="Тут как дома",
        description="Сыграй в 3 разных дня недели.",
        category=Cat.WEEKLY,
        period="week",
        target=3,
        reward_pts=150,
        rule=RULE_DISTINCT_WEEKDAYS,
        unit=UNIT_DAYS,
        sort_order=10,
    ),
    AchievementSpec(
        code="week_hours_10",
        title="Десятка за неделю",
        description="Отыграй 10 часов за неделю.",
        category=Cat.WEEKLY,
        period="week",
        target=600,
        reward_pts=200,
        rule=RULE_MINUTES,
        unit=UNIT_MINUTES,
        sort_order=11,
    ),
    AchievementSpec(
        code="week_pack_3h",
        title="Пакет 3 часа",
        description="Купи пакет на 3 часа.",
        category=Cat.WEEKLY,
        period="week",
        target=1,
        reward_pts=100,
        rule=RULE_EXTERNAL,
        sort_order=12,
        is_implemented=False,   # нужен вебхук покупок из OASys
    ),
    AchievementSpec(
        code="week_pack_5h",
        title="Пакет 5 часов",
        description="Купи пакет на 5 часов.",
        category=Cat.WEEKLY,
        period="week",
        target=1,
        reward_pts=150,
        rule=RULE_EXTERNAL,
        sort_order=13,
        is_implemented=False,   # нужен вебхук покупок из OASys
    ),
    AchievementSpec(
        code="week_bar_orders",
        title="Подкрепись",
        description="Сделай 2 заказа в баре через личный кабинет.",
        category=Cat.WEEKLY,
        period="week",
        target=2,
        reward_pts=100,
        rule=RULE_EXTERNAL,
        sort_order=14,
        is_implemented=False,   # вне MVP: нет интеграции с Эвотором
    ),
    AchievementSpec(
        code="week_booked_play",
        title="По расписанию",
        description="Сыграй по предварительной брони.",
        category=Cat.WEEKLY,
        period="week",
        target=1,
        reward_pts=100,
        rule=RULE_EXTERNAL,
        sort_order=15,
        is_implemented=False,   # нужен вебхук брони из OASys
    ),

    # --- Ежемесячные ---
    AchievementSpec(
        code="month_days_15",
        title="Завсегдатай",
        description="Загляни в клуб в 15 разных дней за месяц.",
        category=Cat.MONTHLY,
        period="month",
        target=15,
        reward_pts=500,
        rule=RULE_DISTINCT_DAYS,
        unit=UNIT_DAYS,
        sort_order=20,
    ),
    AchievementSpec(
        code="month_all_zones",
        title="Исследователь",
        description="Сыграй во всех 5 зонах клуба за месяц.",
        category=Cat.MONTHLY,
        period="month",
        target=5,
        reward_pts=400,
        rule=RULE_DISTINCT_ZONE_TYPES,
        unit=UNIT_ZONES,
        sort_order=21,
    ),
    AchievementSpec(
        code="month_bar_orders",
        title="Гурман месяца",
        description="Сделай 8 заказов в баре за месяц.",
        category=Cat.MONTHLY,
        period="month",
        target=8,
        reward_pts=400,
        rule=RULE_EXTERNAL,
        sort_order=22,
        is_implemented=False,   # вне MVP: нет интеграции с Эвотором
    ),
    AchievementSpec(
        code="month_tariff_daytime",
        title="Совиный режим",
        description="Купи тарифы УТРО, ДЕНЬ и НОЧЬ в течение месяца.",
        category=Cat.MONTHLY,
        period="month",
        target=3,
        reward_pts=400,
        rule=RULE_EXTERNAL,
        sort_order=23,
        is_implemented=False,   # нужен тип тарифа в вебхуке сессии
    ),

    # --- Особые ---
    AchievementSpec(
        code="special_referral",
        title="Зови своих",
        description="Приведи друга по своей ссылке — засчитаем, когда он отыграет час.",
        category=Cat.SPECIAL,
        period="all",
        target=1,
        reward_pts=500,
        rule=RULE_REFERRAL,
        sort_order=30,
    ),
    AchievementSpec(
        code="special_channel_sub",
        title="На связи",
        description="Подпишись на телеграм-канал клуба.",
        category=Cat.SPECIAL,
        period="all",
        target=1,
        reward_pts=100,
        rule=RULE_CHANNEL_SUB,
        sort_order=31,
    ),
    AchievementSpec(
        code="special_pts_5000",
        title="Копилка",
        description="Заработай 5000 PTS за всё время.",
        category=Cat.SPECIAL,
        period="all",
        target=5000,
        reward_pts=1000,
        rule=RULE_PTS_EARNED,
        unit=UNIT_PTS,
        sort_order=32,
    ),
    AchievementSpec(
        code="special_survey",
        title="Есть что сказать",
        description="Пройди опрос клуба.",
        category=Cat.SPECIAL,
        period="all",
        target=1,
        reward_pts=200,
        rule=RULE_EXTERNAL,
        sort_order=33,
        is_implemented=False,   # нужна форма опроса
    ),
)

BY_CODE: dict[str, AchievementSpec] = {a.code: a for a in ACHIEVEMENTS}

DAILY_CHECKIN_CODE = "daily_checkin"
