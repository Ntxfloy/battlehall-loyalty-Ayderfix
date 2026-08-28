"""«ЛУДЛЕНТА» — прокрутка за PTS со случайным призом.

Победитель определяется на сервере: клиент получает уже готовый результат
и ленту для анимации. Иначе выигрыш можно было бы подделать в браузере.

Каждое денежное движение прокрутки привязано к id строки спина: повторный
запуск той же операции не может начислить приз или вернуть ставку дважды.
"""

import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    PrizeKind,
    Reward,
    RewardRedemption,
    TxReason,
    User,
    Wheel,
    WheelPrize,
    WheelSpin,
)
from app.services import achievements, pts, rewards

REEL_LENGTH = 60
WINNER_INDEX = REEL_LENGTH - 8
SPIN_COUNTS: tuple[int, ...] = (1, 5, 10)
ALL_IN_CAP = 20


class WheelError(Exception):
    pass


def list_wheels(db: Session, only_active: bool = True) -> list[Wheel]:
    stmt = select(Wheel).order_by(Wheel.sort_order, Wheel.id)
    if only_active:
        stmt = stmt.where(Wheel.is_active.is_(True))
    return list(db.execute(stmt).scalars())


def prizes_of(db: Session, wheel_id: int, only_active: bool = True) -> list[WheelPrize]:
    stmt = select(WheelPrize).where(WheelPrize.wheel_id == wheel_id).order_by(
        WheelPrize.sort_order, WheelPrize.id
    )
    if only_active:
        stmt = stmt.where(WheelPrize.is_active.is_(True))
    return list(db.execute(stmt).scalars())


def chance_of(prize: WheelPrize, pool: list[WheelPrize]) -> float:
    total = sum(p.weight for p in pool if p.weight > 0)
    if total <= 0:
        return 0.0
    return round(prize.weight / total * 100, 2)


def _pick(pool: list[WheelPrize]) -> WheelPrize:
    weights = [max(p.weight, 0) for p in pool]
    total = sum(weights)
    if total <= 0:
        raise WheelError("У ленты не настроены веса призов")

    roll = secrets.randbelow(total)
    upto = 0
    for prize, weight in zip(pool, weights):
        upto += weight
        if roll < upto:
            return prize
    return pool[-1]


def _build_reel(pool: list[WheelPrize], winner: WheelPrize) -> list[dict]:
    reel = []
    for index in range(REEL_LENGTH):
        prize = winner if index == WINNER_INDEX else _pick(pool)
        reel.append({"title": prize.title, "rarity": prize.rarity, "kind": prize.kind})
    return reel


def _refund_broken_prize(db: Session, user: User, wheel: Wheel, spin_row: WheelSpin) -> None:
    """Последняя линия защиты для битых данных из seed/БД.

    Каталог валидирует призы на записи, но прямое изменение БД не должно
    превращаться в списание без результата для гостя.
    """
    pts.credit(
        db,
        user,
        wheel.cost_pts,
        reason=TxReason.REWARD_REFUND,
        ref_type="wheel",
        ref_id=str(wheel.id),
        comment="Приз настроен неверно, прокрутка возвращена",
        idem_key=f"wheel_refund:{spin_row.id}",
    )
    spin_row.prize_title = "Приз недоступен, PTS возвращены"
    spin_row.prize_kind = PrizeKind.NOTHING
    spin_row.pts_won = 0


def _resolve_one(db: Session, user: User, wheel: Wheel, pool: list[WheelPrize]) -> dict:
    winner = _pick(pool)

    spin_row = WheelSpin(
        user_id=user.id,
        wheel_id=wheel.id,
        prize_id=winner.id,
        cost_pts=wheel.cost_pts,
        prize_title=winner.title,
        prize_kind=winner.kind,
        prize_rarity=winner.rarity,
        pts_won=0,
    )
    # Строка спина сохраняется ДО начисления: её id — это ключ идемпотентности
    # для выигрыша и возврата, без него денежную операцию не отличить от повтора.
    db.add(spin_row)
    db.flush()

    redemption = None
    if winner.kind == PrizeKind.PTS and winner.pts_amount > 0:
        pts.credit(
            db,
            user,
            winner.pts_amount,
            reason=TxReason.WHEEL_PRIZE,
            ref_type="wheel_prize",
            ref_id=str(winner.id),
            comment=f"Выигрыш: {winner.title}",
            idem_key=f"wheel_prize:{spin_row.id}",
        )
        spin_row.pts_won = winner.pts_amount

    elif winner.kind == PrizeKind.REWARD and winner.reward_id:
        reward = db.get(Reward, winner.reward_id)
        if reward is None or not reward.is_active:
            _refund_broken_prize(db, user, wheel, spin_row)
        else:
            redemption = rewards.issue_code(db, user, reward, source="wheel")
            spin_row.redemption_id = redemption.id

    elif winner.kind != PrizeKind.NOTHING:
        _refund_broken_prize(db, user, wheel, spin_row)

    db.add(spin_row)
    db.flush()

    return {
        "spin_id": spin_row.id,
        "reel": _build_reel(pool, winner),
        "winning_index": WINNER_INDEX,
        "prize": {
            "title": spin_row.prize_title,
            "kind": spin_row.prize_kind,
            "rarity": spin_row.prize_rarity,
            "pts_won": spin_row.pts_won,
            "code": redemption.code if redemption else None,
        },
    }


def spin(
    db: Session,
    user: User,
    wheel_id: int,
    count: int = 1,
    all_in: bool = False,
    idem_key: str | None = None,
) -> dict:
    """Одна пачка прокруток.

    `idem_key` — ключ с клиента (заголовок Idempotency-Key). Мини-апп живёт
    в мобильной сети, где ответ легко теряется после того, как сервер уже
    списал PTS. Повтор с тем же ключом не списывает ставку второй раз.
    """
    wheel = db.get(Wheel, wheel_id)
    if wheel is None or not wheel.is_active:
        raise WheelError("Лента недоступна")
    if wheel.cost_pts <= 0:
        raise WheelError("Лента настроена неверно: стоимость должна быть больше нуля")

    debit_key = f"wheel_spin:{user.id}:{idem_key}" if idem_key else None
    if debit_key is not None and pts.find_by_key(db, debit_key) is not None:
        # Ставка по этому ключу уже списана и призы уже выданы. Переигрывать
        # ленту нельзя: гость получил бы второй приз бесплатно.
        raise WheelError("Эта прокрутка уже засчитана, обнови экран")

    pool = prizes_of(db, wheel.id)
    if not pool:
        raise WheelError("У ленты пока нет призов")

    # Mini App и каталог показывают один активный код. Пакетная прокрутка ленты,
    # из которой могут выпасть коды, могла создать сразу несколько pending-кодов
    # и сделать остальные невидимыми. Проверяем инвариант до любого списания.
    has_reward_prizes = any(
        prize.kind == PrizeKind.REWARD and prize.weight > 0 for prize in pool
    )
    if has_reward_prizes and rewards.active_redemption(db, user) is not None:
        raise WheelError("У тебя уже есть активный код — сначала используй его")

    if all_in:
        count = user.pts_balance // wheel.cost_pts
        if count < 1:
            raise WheelError(f"Не хватает PTS даже на одну прокрутку: нужно {wheel.cost_pts}, на балансе {user.pts_balance}")
        if count > ALL_IN_CAP:
            count = ALL_IN_CAP
    elif count not in SPIN_COUNTS:
        raise WheelError("Неверное количество прокруток")

    if has_reward_prizes and count > 1:
        raise WheelError("Для ленты с кодами доступна только одна прокрутка за раз")

    total_cost = wheel.cost_pts * count
    if all_in:
        comment = f"Прокрутка ALL IN x{count}: {wheel.title}"
    elif count > 1:
        comment = f"Прокрутка x{count}: {wheel.title}"
    else:
        comment = f"Прокрутка: {wheel.title}"
    try:
        pts.debit(
            db,
            user,
            total_cost,
            reason=TxReason.REWARD_REDEEM,
            ref_type="wheel",
            ref_id=str(wheel.id),
            comment=comment,
            idem_key=debit_key,
        )
    except pts.InsufficientFunds as exc:
        raise WheelError(f"Не хватает PTS: {exc}") from exc

    spins = [_resolve_one(db, user, wheel, pool) for _ in range(count)]
    achievements.on_pts_changed(db, user)

    return {
        "balance": user.pts_balance,
        "cost_pts": total_cost,
        "count": count,
        "all_in": all_in,
        "spins": spins,
    }


def history(db: Session, user_id: int, limit: int = 30) -> list[WheelSpin]:
    return list(
        db.execute(
            select(WheelSpin)
            .where(WheelSpin.user_id == user_id)
            .order_by(WheelSpin.created_at.desc(), WheelSpin.id.desc())
            .limit(limit)
        ).scalars()
    )


def stats(db: Session, wheel_id: int) -> dict:
    """Фактическая отдача ленты. PTS и рублёвые призы считаются отдельно:
    иначе владелец видит 12% при реальной отдаче в сотни процентов."""
    spins = list(
        db.execute(select(WheelSpin).where(WheelSpin.wheel_id == wheel_id)).scalars()
    )
    spent = sum(s.cost_pts for s in spins)
    won = sum(s.pts_won for s in spins)
    redemption_ids = [s.redemption_id for s in spins if s.redemption_id]
    payouts: dict[int, float] = {}
    if redemption_ids:
        rows = db.execute(
            select(RewardRedemption).where(RewardRedemption.id.in_(redemption_ids))
        ).scalars()
        payouts = {
            r.id: float(r.payout_value or 0)
            for r in rows
            if r.payout_unit == "RUB"
        }
    reward_payout_value = sum(
        payouts.get(s.redemption_id, 0) for s in spins if s.redemption_id
    )
    by_prize: dict[str, int] = {}
    for s in spins:
        by_prize[s.prize_title] = by_prize.get(s.prize_title, 0) + 1

    return {
        "spins": len(spins),
        "pts_spent": spent,
        "pts_won": won,
        "reward_payout_value": reward_payout_value,
        "payout_percent": round(won / spent * 100, 1) if spent else 0.0,
        "by_prize": by_prize,
    }
