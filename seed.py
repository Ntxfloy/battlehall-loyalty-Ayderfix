"""Наполнение справочников: достижения и каталог наград.

Запуск: python seed.py [--force]

Без --force скрипт только добавляет недостающие записи и не трогает уже
существующие: цели и награды правит админ, и релиз не должен затирать его правки.
С --force значения перезаписываются из кода.

ВНИМАНИЕ: цены наград и суммы PTS ниже — заглушки по мотивам скриншотов
Oblaka Gaming. Реальную экономику должен утвердить клуб.
"""

import sys

from sqlalchemy import select

from app.achievements_defs import ACHIEVEMENTS, DAILY_CHECKIN_CODE
from app.admin_auth import hash_password
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import (
    AchievementDef,
    AdminRole,
    AdminUser,
    Club,
    PrizeKind,
    PrizeRarity,
    Reward,
    RewardKind,
    Wheel,
    WheelPrize,
)

settings = get_settings()

REWARDS = (
    {
        "code": "cash_300",
        "kind": RewardKind.CASH,
        "title": "300 ₽ на игровой счёт",
        "description": "Администратор пополнит твой игровой баланс на 300 ₽.",
        "cost_pts": 3000,
        "payout_value": 300,
        "payout_unit": "RUB",
        "sort_order": 10,
    },
    {
        "code": "cash_500",
        "kind": RewardKind.CASH,
        "title": "500 ₽ на игровой счёт",
        "description": "Выгоднее, чем два обмена по 300 ₽.",
        "cost_pts": 4750,
        "payout_value": 500,
        "payout_unit": "RUB",
        "sort_order": 11,
    },
    {
        "code": "cash_1000",
        "kind": RewardKind.CASH,
        "title": "1000 ₽ на игровой счёт",
        "description": "Максимальная скидка при обмене.",
        "cost_pts": 9000,
        "payout_value": 1000,
        "payout_unit": "RUB",
        "sort_order": 12,
    },
    {
        "code": "premium_3m",
        "kind": RewardKind.TELEGRAM_PREMIUM,
        "title": "Telegram Premium, 3 месяца",
        "description": "Подписку активирует администратор на твой аккаунт.",
        "cost_pts": 12000,
        "payout_value": 3,
        "payout_unit": "MONTHS",
        "sort_order": 20,
    },
    {
        "code": "premium_12m",
        "kind": RewardKind.TELEGRAM_PREMIUM,
        "title": "Telegram Premium, 1 год",
        "description": "Подписку активирует администратор на твой аккаунт.",
        "cost_pts": 38000,
        "payout_value": 12,
        "payout_unit": "MONTHS",
        "sort_order": 21,
    },
)


def seed_achievements(db, force: bool) -> tuple[int, int]:
    created = updated = 0
    for spec in ACHIEVEMENTS:
        row = db.execute(
            select(AchievementDef).where(AchievementDef.code == spec.code)
        ).scalar_one_or_none()

        reward_pts = spec.reward_pts
        if spec.code == DAILY_CHECKIN_CODE:
            reward_pts = settings.daily_checkin_pts

        if row is None:
            db.add(
                AchievementDef(
                    code=spec.code,
                    title=spec.title,
                    description=spec.description,
                    category=spec.category,
                    period=spec.period,
                    target=spec.target,
                    reward_pts=reward_pts,
                    unit=spec.unit,
                    sort_order=spec.sort_order,
                    is_implemented=spec.is_implemented,
                )
            )
            created += 1
        elif force:
            row.title = spec.title
            row.description = spec.description
            row.category = spec.category
            row.period = spec.period
            row.target = spec.target
            row.reward_pts = reward_pts
            row.unit = spec.unit
            row.sort_order = spec.sort_order
            row.is_implemented = spec.is_implemented
            db.add(row)
            updated += 1
    return created, updated


def seed_rewards(db, force: bool) -> tuple[int, int]:
    created = updated = 0
    for item in REWARDS:
        row = db.execute(
            select(Reward).where(Reward.code == item["code"])
        ).scalar_one_or_none()
        if row is None:
            db.add(Reward(**item))
            created += 1
        elif force:
            for key, value in item.items():
                setattr(row, key, value)
            db.add(row)
            updated += 1
    return created, updated


def seed_default_club(db) -> Club | None:
    """Создаёт клуб "main" из значений .env — только если в базе ещё нет
    ни одного клуба. Дальше клубы заводятся из панели, а не отсюда."""
    exists = db.execute(select(Club.id)).first()
    if exists:
        return None
    club = Club(
        slug="main",
        name="BATTLEHALL",
        oasys_webhook_token=settings.oasys_webhook_token,
    )
    db.add(club)
    return club


def ensure_owner(db) -> AdminUser | None:
    """Гарантирует, что в системе есть владелец.

    Учётки, заведённые до появления ролей, получили роль по умолчанию
    («сотрудник») — без этой починки панель осталась бы без владельца,
    и управлять правами стало бы некому."""
    has_owner = db.execute(select(AdminUser.id).where(AdminUser.role == AdminRole.OWNER)).first()
    if has_owner:
        return None

    first = db.execute(select(AdminUser).order_by(AdminUser.id)).scalars().first()
    if first is None:
        return None

    first.role = AdminRole.OWNER
    db.add(first)
    return first


def seed_default_admin(db) -> AdminUser | None:
    """Создаёт первую учётку администратора из ADMIN_DEFAULT_USERNAME/PASSWORD —
    только если в базе ещё нет ни одного админа. Дальше учётки заводятся
    из панели, в разделе «Администраторы»."""
    exists = db.execute(select(AdminUser.id)).first()
    if exists:
        return None
    admin = AdminUser(
        username=settings.admin_default_username,
        password_hash=hash_password(settings.admin_default_password),
        display_name="Владелец",
        role=AdminRole.OWNER,
    )
    db.add(admin)
    return admin


def seed_default_wheel(db) -> Wheel | None:
    """Стартовая «ЛУДЛЕНТА» — чтобы механику было на чём показать.
    Состав и веса дальше правятся в админке, а не здесь.

    Веса подобраны так, чтобы средняя отдача была ниже стоимости прокрутки:
    100·40 + 250·30 + 600·15 + 1500·5 = 26 500 PTS на 100 прокруток по 300 =
    30 000 PTS, то есть примерно 88%. Экономику всё равно должен утвердить клуб."""
    exists = db.execute(select(Wheel.id)).first()
    if exists:
        return None

    wheel = Wheel(
        code="daily_loot",
        title="ЛУДЛЕНТА",
        description="Прокрути ленту и забери случайный приз.",
        cost_pts=300,
        sort_order=10,
    )
    db.add(wheel)
    db.flush()

    prizes = (
        ("100 PTS", PrizeKind.PTS, PrizeRarity.COMMON, 100, 40),
        ("250 PTS", PrizeKind.PTS, PrizeRarity.COMMON, 250, 30),
        ("600 PTS", PrizeKind.PTS, PrizeRarity.RARE, 600, 15),
        ("1500 PTS", PrizeKind.PTS, PrizeRarity.EPIC, 1500, 5),
        ("Пусто", PrizeKind.NOTHING, PrizeRarity.COMMON, 0, 10),
    )
    for order, (title, kind, rarity, amount, weight) in enumerate(prizes):
        db.add(
            WheelPrize(
                wheel_id=wheel.id,
                title=title,
                kind=kind,
                rarity=rarity,
                pts_amount=amount,
                weight=weight,
                sort_order=order,
            )
        )
    return wheel


def main() -> None:
    force = "--force" in sys.argv
    init_db()
    with SessionLocal() as db:
        a_created, a_updated = seed_achievements(db, force)
        r_created, r_updated = seed_rewards(db, force)
        club = seed_default_club(db)
        admin = seed_default_admin(db)
        promoted = ensure_owner(db)
        wheel = seed_default_wheel(db)
        db.commit()

    print(f"Достижения: добавлено {a_created}, обновлено {a_updated}")
    print(f"Награды:    добавлено {r_created}, обновлено {r_updated}")
    if club:
        print(f"Клуб по умолчанию создан: slug=main, токен вебхука = OASYS_WEBHOOK_TOKEN из .env")
    if admin:
        print(
            f"Владелец создан: логин «{settings.admin_default_username}», "
            f"пароль — тот, что в ADMIN_DEFAULT_PASSWORD (.env). Смени его после первого входа."
        )
    if promoted:
        print(f"Учётка «{promoted.username}» повышена до владельца — в системе не было ни одного.")
    if wheel:
        print("ЛУДЛЕНТА создана: код daily_loot, 300 PTS за прокрутку")
    if not force:
        print("Существующие записи не тронуты. Перезаписать значения из кода: python seed.py --force")


if __name__ == "__main__":
    main()
