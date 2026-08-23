"""Служебные ручки администратора для наград и кодов.

Доступ — либо сервисный токен `X-Admin-Token` (скрипты, выгрузка), либо
кука сессии панели (браузер), см. app/admin_auth.py.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import permissions as perms
from app.admin_auth import Caller, require_admin_permission
from app.db import get_db
from app.models import RedemptionStatus, RewardRedemption, User
from app.periods import iso
from app.schemas import GrantPtsRequest, UseCodeRequest
from app.services import audit, rewards

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/redemptions/{code}")
def lookup_code(
    code: str,
    _: Caller = Depends(require_admin_permission(perms.CODES_VIEW)),
    db: Session = Depends(get_db),
) -> dict:
    """Что за код принёс гость: какая награда, кому принадлежит, жив ли."""
    row = rewards.lookup(db, code)
    if row is None:
        raise HTTPException(status_code=404, detail="Код не найден")
    user = db.get(User, row.user_id)
    return {
        "code": row.code,
        "status": rewards.effective_status(row),
        "reward": row.reward_title,

        "payout_value": float(row.payout_value),
        "payout_unit": row.payout_unit,
        "pts_spent": row.pts_spent,
        "created_at": iso(row.created_at),
        "expires_at": iso(row.expires_at),
        "used_at": iso(row.used_at),
        "guest": {
            "telegram_id": user.telegram_id if user else None,
            "username": user.username if user else None,
            "first_name": user.first_name if user else None,
            "phone": user.phone if user else None,
        },
    }


@router.post("/redemptions/use")
def use_code(
    body: UseCodeRequest,
    caller: Caller = Depends(require_admin_permission(perms.CODES_SUBMIT)),
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = rewards.use_code(db, body.code, caller.label)
    except rewards.RewardError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.log(db, caller.label, "redemption_use", target_type="redemption", target_id=row.code,
              detail={"reward": row.reward_title, "pts_spent": row.pts_spent})
    db.commit()
    return {"ok": True, "code": row.code, "reward": row.reward_title, "used_at": iso(row.used_at)}


@router.get("/redemptions")
def list_redemptions(
    status: str = RedemptionStatus.APPROVED,
    only_new: bool = True,
    _: Caller = Depends(require_admin_permission(perms.REPORTS_EXPORT)),
    db: Session = Depends(get_db),
) -> dict:
    """Выгрузка для гугл-таблицы компенсаций.

    По умолчанию отдаёт только подтверждённые владельцем строки: внесённый
    сотрудником код (`submitted`) в таблицу попадать не должен, пока его
    не аппрувнули.

    `only_new=true` отдаёт то, что ещё не выгружали, — чтобы не дублировать строки.
    """
    stmt = select(RewardRedemption).where(RewardRedemption.status == status)
    if only_new:
        stmt = stmt.where(RewardRedemption.exported_at.is_(None))
    rows = list(db.execute(stmt.order_by(RewardRedemption.created_at)).scalars())

    items = []
    for row in rows:
        user = db.get(User, row.user_id)
        items.append(
            {
                "code": row.code,
                "telegram_id": user.telegram_id if user else None,
                "username": user.username if user else None,
                "phone": user.phone if user else None,
                "reward": row.reward_title,
                "payout_value": float(row.payout_value),
                "payout_unit": row.payout_unit,
                "pts_spent": row.pts_spent,
                "used_at": iso(row.used_at),
            }
        )
    return {"items": items}


@router.post("/redemptions/mark-exported")
def mark_exported(
    codes: list[str],
    caller: Caller = Depends(require_admin_permission(perms.REPORTS_EXPORT)),
    db: Session = Depends(get_db),
) -> dict:
    now = datetime.now(timezone.utc)
    updated = 0
    for code in codes:
        row = rewards.lookup(db, code)
        if row is not None and row.exported_at is None:
            row.exported_at = now
            db.add(row)
            updated += 1
    audit.log(db, caller.label, "redemptions_mark_exported", detail={"count": updated})
    db.commit()
    return {"ok": True, "updated": updated}


@router.post("/pts/grant")
def grant_pts(
    body: GrantPtsRequest,
    caller: Caller = Depends(require_admin_permission(perms.PTS_GRANT, allow_service_token=False)),
    db: Session = Depends(get_db),
) -> dict:
    user = db.query(User).filter(User.telegram_id == body.telegram_id).one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    rewards.grant_pts(db, user, body.amount, body.comment)
    audit.log(db, caller.label, "pts_grant", target_type="user", target_id=str(user.telegram_id),
              detail={"amount": body.amount, "comment": body.comment})
    db.commit()
    return {"ok": True, "balance": user.pts_balance}


@router.post("/maintenance/expire-codes")
def expire_codes(
    caller: Caller = Depends(require_admin_permission(perms.MAINTENANCE_RUN)),
    db: Session = Depends(get_db),
) -> dict:
    """Обычно коды гасятся лениво при обращении к наградам.
    Эта ручка нужна, чтобы прогнать всех разом (например, из cron)."""
    count = rewards.expire_due(db)
    audit.log(db, caller.label, "maintenance_expire_codes", detail={"count": count})
    db.commit()
    return {"expired": count}
