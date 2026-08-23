"""API для Mini App: всё, что видит гость в четырёх вкладках."""

from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import current_user
from app.config import get_settings
from app.db import get_db
from app.loyalty import group_for_hours, next_group
from app.models import RedemptionStatus, User
from app.periods import ensure_utc, iso
from app.schemas import ClaimRequest, RedeemRequest, SpinRequest
from app.services import achievements, pts, referrals, rewards, sessions
from app.zones import zone_title

router = APIRouter(prefix="/api", tags=["miniapp"])
settings = get_settings()

CHANNEL_ACHIEVEMENT = "special_channel_sub"


def _group_payload(hours_year: float) -> dict:
    group = group_for_hours(hours_year)
    nxt = next_group(group)
    return {
        "level": group.level,
        "title": f"{group.level}. {group.title}",
        "discount_percent": group.discount_percent,
        "hours_year": hours_year,
        "next": {
            "level": nxt.level,
            "title": f"{nxt.level}. {nxt.title}",
            "discount_percent": nxt.discount_percent,
            "hours_required": nxt.min_hours_year,
            "hours_left": round(max(nxt.min_hours_year - hours_year, 0), 1),
            "percent": min(round(hours_year / nxt.min_hours_year * 100), 100)
            if nxt.min_hours_year
            else 100,
        }
        if nxt
        else None,
    }


def _redemption_payload(row, now: datetime | None = None) -> dict:
    if now is None:
        now = datetime.now(timezone.utc)
    return {
        "code": row.code,
        "status": rewards.effective_status(row, now),
        "title": row.reward_title,
        "pts_spent": row.pts_spent,
        "payout_value": float(row.payout_value),
        "payout_unit": row.payout_unit,
        "created_at": iso(row.created_at),
        "expires_at": iso(row.expires_at),
        "used_at": iso(row.used_at),
    }




@router.get("/me")
def get_me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    year = datetime.now(timezone.utc).year
    stats = sessions.year_stats(db, user.id, year)
    overview = achievements.overview(db, user)
    daily = overview.get("daily") or []

    return {
        "user": {
            "telegram_id": user.telegram_id,
            "first_name": user.first_name,
            "username": user.username,
            "phone_linked": bool(user.phone),
        },
        "balance": user.pts_balance,
        "group": _group_payload(stats["hours"]),
        "stats": {
            "visits_year": stats["visits"],
            "hours_year": stats["hours"],
            "achievements_completed": achievements.completed_count(db, user),
        },
        "daily": daily[0] if daily else None,
    }


@router.get("/achievements")
def get_achievements(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    overview = achievements.overview(db, user)
    return {
        "weekly": overview.get("weekly", []),
        "monthly": overview.get("monthly", []),
        "special": overview.get("special", []),
        "daily": overview.get("daily", []),
    }


@router.post("/achievements/claim")
def claim_achievement(
    body: ClaimRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = achievements.claim(db, user, body.code)
    except achievements.AchievementError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return {"ok": True, "credited_pts": row.reward_pts, "balance": user.pts_balance}


@router.post("/achievements/check-subscription")
def check_subscription(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Ачивка «На связи»: спрашиваем у Telegram, подписан ли гость на канал."""
    if not settings.bot_token or not settings.channel_id:
        raise HTTPException(status_code=503, detail="Канал не настроен на сервере")

    url = f"https://api.telegram.org/bot{settings.bot_token}/getChatMember"
    try:
        response = httpx.get(
            url,
            params={"chat_id": settings.channel_id, "user_id": user.telegram_id},
            timeout=10,
        )
        data = response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Telegram не отвечает") from exc

    if not data.get("ok"):
        raise HTTPException(status_code=502, detail="Не удалось проверить подписку")

    status_value = data["result"].get("status")
    subscribed = status_value in {"creator", "administrator", "member"}
    if subscribed:
        achievements.mark_completed(db, user, CHANNEL_ACHIEVEMENT)
        db.commit()
    return {"subscribed": subscribed}


@router.get("/rewards")
def get_rewards(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    now = datetime.now(timezone.utc)
    active = rewards.active_redemption(db, user)
    return {
        "balance": user.pts_balance,
        "items": [
            {
                "id": r.id,
                "code": r.code,
                "kind": r.kind,
                "title": r.title,
                "description": r.description,
                "cost_pts": r.cost_pts,
                "payout_value": float(r.payout_value),
                "payout_unit": r.payout_unit,
                "affordable": user.pts_balance >= r.cost_pts,
            }
            for r in rewards.catalog(db)
        ],
        "active_code": _redemption_payload(active, now) if active else None,
    }





@router.post("/rewards/redeem")
def redeem_reward(
    body: RedeemRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = rewards.redeem(db, user, body.reward_id)
        db.commit()
    except rewards.RewardError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "balance": user.pts_balance, "redemption": _redemption_payload(row)}



@router.get("/redemptions")
def get_redemptions(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return {"items": [_redemption_payload(r) for r in rewards.history(db, user)]}


@router.get("/history/visits")
def get_visits(
    year: int | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    years = sessions.available_years(db, user.id)
    rows = sessions.visit_history(db, user.id, year)
    return {
        "years": years,
        "selected_year": year,
        "stats": sessions.year_stats(db, user.id, year) if year else None,
        "items": [
            {
                "date": r.game_day,
                "started_at": iso(r.started_at),
                "ended_at": iso(r.ended_at),
                "pc_number": r.pc_number,
                "zone": zone_title(r.zone_code),
                "zone_type": r.zone_type,
                "minutes": r.duration_minutes,
                "hours": round(r.duration_minutes / 60, 1),
                "is_closed": r.is_closed,
            }
            for r in rows
        ],
    }


@router.get("/history/pts")
def get_pts_history(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return {
        "balance": user.pts_balance,
        "items": [
            {
                "amount": t.amount,
                "balance_after": t.balance_after,
                "reason": t.reason,
                "comment": t.comment,
                "created_at": iso(t.created_at),
            }
            for t in pts.history(db, user.id)
        ],
    }


@router.get("/referral")
def get_referral(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return referrals.summary(db, user)


@router.get("/rules")
def get_rules() -> dict:
    """Правила и FAQ. Текст живёт здесь, чтобы правился без пересборки фронта."""
    return {
        "rules": [
            "Игровой день начинается в 06:00 МСК — ночная сессия относится к предыдущему дню.",
            "Еженедельный прогресс обнуляется в понедельник в 06:00 МСК, ежемесячный — первого числа.",
            "Награду за достижение нужно забрать вручную до конца периода, иначе она сгорит вместе с прогрессом.",
            f"Код награды действует {settings.reward_code_ttl_hours} часа и показывается только внутри приложения.",
            "Код привязан к твоему Telegram-аккаунту: администратор сверяет его с тем, кто стоит перед ним.",
        ],
        "faq": [
            {
                "q": "Почему не начислились часы за сессию?",
                "a": "Часы засчитываются после закрытия сессии в клубе. Если прошло больше часа — напиши администратору.",
            },
            {
                "q": "Что будет, если я не успею использовать код?",
                "a": "Код сгорит через "
                + str(settings.reward_code_ttl_hours)
                + " часа. PTS "
                + ("вернутся на баланс автоматически." if settings.refund_pts_on_expire else "не возвращаются."),
            },
            {
                "q": "Как засчитывается приглашённый друг?",
                "a": f"Друг должен перейти по твоей ссылке и отыграть минимум {settings.referral_min_minutes} минут.",
            },
        ],
    }


@router.get("/wheels")
def get_wheels(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Ленты и их состав. Шансы показываем честно — так механика
    не выглядит чёрным ящиком и меньше поводов для споров."""
    from app.services import wheel as wheel_service

    items = []
    for row in wheel_service.list_wheels(db):
        prizes = wheel_service.prizes_of(db, row.id)
        items.append(
            {
                "id": row.id,
                "code": row.code,
                "title": row.title,
                "description": row.description,
                "cost_pts": row.cost_pts,
                "affordable": user.pts_balance >= row.cost_pts,
                "prizes": [
                    {
                        "title": p.title,
                        "kind": p.kind,
                        "rarity": p.rarity,
                        "pts_amount": p.pts_amount,
                        "chance": wheel_service.chance_of(p, prizes),
                    }
                    for p in prizes
                ],
            }
        )
    return {"balance": user.pts_balance, "items": items}


@router.post("/wheels/spin")
def spin_wheel(
    body: SpinRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    from app.services import wheel as wheel_service

    try:
        res = wheel_service.spin(db, user, body.wheel_id, body.count, body.all_in)
        db.commit()
        return res
    except wheel_service.WheelError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc



@router.get("/wheels/history")
def wheel_history(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    from app.services import wheel as wheel_service

    return {
        "items": [
            {
                "title": s.prize_title,
                "kind": s.prize_kind,
                "rarity": s.prize_rarity,
                "pts_won": s.pts_won,
                "cost_pts": s.cost_pts,
                "created_at": iso(s.created_at),
            }
            for s in wheel_service.history(db, user.id)
        ]
    }
