"""Редактирование каталога из админки: достижения, награды, «ЛУДЛЕНТА».

Правило разделения: тексты, цели, награды и веса — данные, их правит владелец
без релиза. Способ подсчёта достижения (`rule`) остаётся в коде
(app/achievements_defs.py) — это логика, а не настройка.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.achievements_defs import BY_CODE
from app.models import AchievementDef, PrizeKind, Reward, Wheel, WheelPrize


class CatalogError(Exception):
    pass


# --- достижения ---

def list_achievements(db: Session) -> list[AchievementDef]:
    return list(
        db.execute(select(AchievementDef).order_by(AchievementDef.sort_order, AchievementDef.id)).scalars()
    )


def achievement_payload(row: AchievementDef) -> dict:
    spec = BY_CODE.get(row.code)
    return {
        "id": row.id,
        "code": row.code,
        "title": row.title,
        "description": row.description,
        "category": row.category,
        "period": row.period,
        "target": row.target,
        "reward_pts": row.reward_pts,
        "unit": row.unit,
        "sort_order": row.sort_order,
        "is_active": row.is_active,
        "is_implemented": row.is_implemented,
        # Показываем в панели, чем именно считается прогресс — иначе непонятно,
        # почему у части достижений нельзя включить автоподсчёт.
        "rule": spec.rule if spec else "external",
    }


def update_achievement(db: Session, achievement_id: int, data: dict) -> AchievementDef:
    row = db.get(AchievementDef, achievement_id)
    if row is None:
        raise CatalogError("Достижение не найдено")

    for field in ("title", "description", "unit"):
        if data.get(field) is not None:
            setattr(row, field, data[field])

    for field in ("target", "reward_pts", "sort_order"):
        if data.get(field) is not None:
            value = int(data[field])
            if field == "target" and value < 1:
                raise CatalogError("Цель должна быть больше нуля")
            if field == "reward_pts" and value < 0:
                raise CatalogError("Награда не может быть отрицательной")
            setattr(row, field, value)

    if data.get("is_active") is not None:
        row.is_active = bool(data["is_active"])

    # is_implemented намеренно не редактируется: подключён счётчик или нет —
    # определяется наличием обработчика в коде, а не галочкой в панели.

    db.add(row)
    db.commit()
    return row


# --- награды ---

def list_rewards(db: Session) -> list[Reward]:
    return list(db.execute(select(Reward).order_by(Reward.sort_order, Reward.id)).scalars())


def reward_payload(row: Reward) -> dict:
    return {
        "id": row.id,
        "code": row.code,
        "kind": row.kind,
        "title": row.title,
        "description": row.description,
        "cost_pts": row.cost_pts,
        "payout_value": float(row.payout_value),
        "payout_unit": row.payout_unit,
        "sort_order": row.sort_order,
        "is_active": row.is_active,
    }


def create_reward(db: Session, data: dict) -> Reward:
    code = (data.get("code") or "").strip()
    if not code:
        raise CatalogError("Нужен код награды")
    if db.execute(select(Reward.id).where(Reward.code == code)).first():
        raise CatalogError(f"Награда с кодом «{code}» уже есть")

    row = Reward(
        code=code,
        kind=data.get("kind") or "cash",
        title=data.get("title") or code,
        description=data.get("description") or "",
        cost_pts=int(data.get("cost_pts") or 0),
        payout_value=float(data.get("payout_value") or 0),
        payout_unit=data.get("payout_unit") or "RUB",
        sort_order=int(data.get("sort_order") or 0),
        is_active=bool(data.get("is_active", True)),
    )
    if row.cost_pts < 0:
        raise CatalogError("Стоимость не может быть отрицательной")
    db.add(row)
    db.commit()
    return row


def update_reward(db: Session, reward_id: int, data: dict) -> Reward:
    row = db.get(Reward, reward_id)
    if row is None:
        raise CatalogError("Награда не найдена")

    for field in ("title", "description", "kind", "payout_unit"):
        if data.get(field) is not None:
            setattr(row, field, data[field])
    for field in ("cost_pts", "sort_order"):
        if data.get(field) is not None:
            setattr(row, field, int(data[field]))
    if data.get("payout_value") is not None:
        row.payout_value = float(data["payout_value"])
    if data.get("is_active") is not None:
        row.is_active = bool(data["is_active"])

    if row.cost_pts < 0:
        raise CatalogError("Стоимость не может быть отрицательной")

    db.add(row)
    db.commit()
    return row


def delete_reward(db: Session, reward_id: int) -> None:
    """Награду не удаляем физически: на неё ссылаются выданные коды и призы
    ленты. Выключаем — из витрины пропадает, история остаётся целой."""
    row = db.get(Reward, reward_id)
    if row is None:
        raise CatalogError("Награда не найдена")
    row.is_active = False
    db.add(row)
    db.commit()


# --- ЛУДЛЕНТА ---

def list_wheels(db: Session) -> list[Wheel]:
    return list(db.execute(select(Wheel).order_by(Wheel.sort_order, Wheel.id)).scalars())


def wheel_payload(db: Session, row: Wheel) -> dict:
    from app.services import wheel as wheel_service

    prizes = wheel_service.prizes_of(db, row.id, only_active=False)
    active = [p for p in prizes if p.is_active]
    return {
        "id": row.id,
        "code": row.code,
        "title": row.title,
        "description": row.description,
        "cost_pts": row.cost_pts,
        "sort_order": row.sort_order,
        "is_active": row.is_active,
        "prizes": [
            {
                "id": p.id,
                "title": p.title,
                "kind": p.kind,
                "rarity": p.rarity,
                "pts_amount": p.pts_amount,
                "reward_id": p.reward_id,
                "weight": p.weight,
                "sort_order": p.sort_order,
                "is_active": p.is_active,
                "chance": wheel_service.chance_of(p, active) if p.is_active else 0.0,
            }
            for p in prizes
        ],
    }


def create_wheel(db: Session, data: dict) -> Wheel:
    code = (data.get("code") or "").strip()
    if not code:
        raise CatalogError("Нужен код ленты")
    if db.execute(select(Wheel.id).where(Wheel.code == code)).first():
        raise CatalogError(f"Лента с кодом «{code}» уже есть")

    row = Wheel(
        code=code,
        title=data.get("title") or code,
        description=data.get("description") or "",
        cost_pts=int(data.get("cost_pts") or 0),
        sort_order=int(data.get("sort_order") or 0),
        is_active=bool(data.get("is_active", True)),
    )
    if row.cost_pts <= 0:
        raise CatalogError("Прокрутка должна что-то стоить")
    db.add(row)
    db.commit()
    return row


def update_wheel(db: Session, wheel_id: int, data: dict) -> Wheel:
    row = db.get(Wheel, wheel_id)
    if row is None:
        raise CatalogError("Лента не найдена")

    for field in ("title", "description"):
        if data.get(field) is not None:
            setattr(row, field, data[field])
    for field in ("cost_pts", "sort_order"):
        if data.get(field) is not None:
            setattr(row, field, int(data[field]))
    if data.get("is_active") is not None:
        row.is_active = bool(data["is_active"])

    if row.cost_pts <= 0:
        raise CatalogError("Прокрутка должна что-то стоить")

    db.add(row)
    db.commit()
    return row


def _validate_prize(data: dict) -> None:
    kind = data.get("kind")
    if kind == PrizeKind.PTS and int(data.get("pts_amount") or 0) <= 0:
        raise CatalogError("Для приза в PTS нужно указать сумму")
    if kind == PrizeKind.REWARD and not data.get("reward_id"):
        raise CatalogError("Для приза-награды нужно выбрать награду из каталога")
    if int(data.get("weight") or 0) < 0:
        raise CatalogError("Вес не может быть отрицательным")


def create_prize(db: Session, wheel_id: int, data: dict) -> WheelPrize:
    if db.get(Wheel, wheel_id) is None:
        raise CatalogError("Лента не найдена")
    _validate_prize(data)

    row = WheelPrize(
        wheel_id=wheel_id,
        title=data.get("title") or "Приз",
        kind=data.get("kind") or PrizeKind.PTS,
        rarity=data.get("rarity") or "common",
        pts_amount=int(data.get("pts_amount") or 0),
        reward_id=data.get("reward_id"),
        weight=int(data.get("weight") or 1),
        sort_order=int(data.get("sort_order") or 0),
        is_active=bool(data.get("is_active", True)),
    )
    db.add(row)
    db.commit()
    return row


def update_prize(db: Session, prize_id: int, data: dict) -> WheelPrize:
    row = db.get(WheelPrize, prize_id)
    if row is None:
        raise CatalogError("Приз не найден")

    merged = {
        "kind": data.get("kind", row.kind),
        "pts_amount": data.get("pts_amount", row.pts_amount),
        "reward_id": data.get("reward_id", row.reward_id),
        "weight": data.get("weight", row.weight),
    }
    _validate_prize(merged)

    for field in ("title", "kind", "rarity"):
        if data.get(field) is not None:
            setattr(row, field, data[field])
    for field in ("pts_amount", "weight", "sort_order"):
        if data.get(field) is not None:
            setattr(row, field, int(data[field]))
    if "reward_id" in data:
        row.reward_id = data["reward_id"] or None
    if data.get("is_active") is not None:
        row.is_active = bool(data["is_active"])

    db.add(row)
    db.commit()
    return row


def delete_prize(db: Session, prize_id: int) -> None:
    row = db.get(WheelPrize, prize_id)
    if row is None:
        raise CatalogError("Приз не найден")
    # История прокруток хранит название приза снимком, поэтому удаление
    # ячейки не ломает прошлые записи.
    row.is_active = False
    db.add(row)
    db.commit()
