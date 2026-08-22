"""Стойка клуба и подтверждение выдач.

Сюда ходит аккаунт с администраторского ПК: находит гостя по коду, нику или
телефону и вносит код. Подтверждает выдачу уже владелец — сотрудник не может
аппрувить сам себя (право `codes.approve` ему выдать нельзя, см. permissions.py).
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import permissions as perms
from app.admin_auth import require_permission
from app.db import get_db
from app.models import AdminUser, RewardRedemption, User
from app.periods import iso
from app.schemas import CodeActionRequest
from app.config import get_settings
from app.services import audit, rewards, sheets

router = APIRouter(prefix="/api/console/desk", tags=["desk"])
settings = get_settings()


def _code_payload(db: Session, row: RewardRedemption) -> dict:
    guest = db.get(User, row.user_id)
    return {
        "code": row.code,
        "status": row.status,
        "reward": row.reward_title,
        "payout_value": float(row.payout_value),
        "payout_unit": row.payout_unit,
        "pts_spent": row.pts_spent,
        "source": row.source,
        "created_at": iso(row.created_at),
        "expires_at": iso(row.expires_at),
        "used_at": iso(row.used_at),
        "used_by": row.used_by,
        "approved_at": iso(row.approved_at),
        "approved_by": row.approved_by,
        "guest": {
            "telegram_id": guest.telegram_id if guest else None,
            "username": guest.username if guest else None,
            "first_name": guest.first_name if guest else None,
            "phone": guest.phone if guest else None,
        },
    }


@router.get("/search")
def search(
    q: str = Query(..., min_length=2, description="код, ник, имя или телефон"),
    _: AdminUser = Depends(require_permission(perms.CODES_VIEW)),
    db: Session = Depends(get_db),
) -> dict:
    """Одно поле поиска: гость на стойке может назвать что угодно из своего."""
    needle = q.strip()

    exact = rewards.lookup(db, needle)
    if exact is not None:
        return {"items": [_code_payload(db, exact)]}

    pattern = f"%{needle}%"
    conditions = [User.username.ilike(pattern), User.first_name.ilike(pattern)]

    digits = "".join(ch for ch in needle if ch.isdigit())
    if digits:
        conditions.append(User.phone.ilike(f"%{digits}%"))
        if needle.isdigit():
            conditions.append(User.telegram_id == int(needle))

    guests = list(db.execute(select(User).where(or_(*conditions)).limit(10)).scalars())

    items: list[dict] = []
    for guest in guests:
        for row in rewards.history(db, guest, limit=10):
            items.append(_code_payload(db, row))
    return {"items": items}


@router.post("/submit")
def submit(
    body: CodeActionRequest,
    admin: AdminUser = Depends(require_permission(perms.CODES_SUBMIT)),
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = rewards.use_code(db, body.code, admin.username)
    except rewards.RewardError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit.log(db, admin.username, "code_submit", target_type="redemption", target_id=row.code,
              detail={"reward": row.reward_title})
    db.commit()
    return {"ok": True, "code": row.code, "status": row.status, "reward": row.reward_title}


@router.get("/queue")
def queue(
    _: AdminUser = Depends(require_permission(perms.CODES_VIEW)),
    db: Session = Depends(get_db),
) -> dict:
    """Коды, внесённые на стойке и ждущие подтверждения владельца."""
    return {"items": [_code_payload(db, row) for row in rewards.pending_approval(db)]}


@router.post("/approve")
def approve(
    body: CodeActionRequest,
    admin: AdminUser = Depends(require_permission(perms.CODES_APPROVE)),
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = rewards.approve_code(db, body.code, admin.username)
    except rewards.RewardError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit.log(db, admin.username, "code_approve", target_type="redemption", target_id=row.code,
              detail={"reward": row.reward_title, "submitted_by": row.used_by})
    db.commit()

    # Пробуем отправить строку в таблицу сразу. Неудача не откатывает
    # подтверждение: строка останется в очереди и уедет следующей выгрузкой.
    exported = False
    if settings.google_autoexport:
        exported = sheets.export_one(db, row)

    return {"ok": True, "code": row.code, "status": row.status, "exported_to_sheets": exported}


@router.post("/reject")
def reject(
    body: CodeActionRequest,
    admin: AdminUser = Depends(require_permission(perms.CODES_APPROVE)),
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = rewards.reject_code(db, body.code, admin.username)
    except rewards.RewardError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit.log(db, admin.username, "code_reject", target_type="redemption", target_id=row.code)
    db.commit()
    return {"ok": True, "code": row.code, "status": row.status}
