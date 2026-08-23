"""Управление содержимым мини-аппа и учётками администраторов.

Всё, что видит гость — достижения, награды, «ЛУДЛЕНТА» — правится отсюда
без релиза. Логика подсчёта достижений остаётся в коде: в панели меняются
тексты, цели, награды и веса, но не способ, которым считается прогресс.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import permissions as perms
from app.admin_auth import current_admin_user, require_permission, verify_password
from app.config import get_settings
from app.db import get_db
from app.models import AdminUser, TxReason, User
from app.schemas import (

    AchievementUpdateRequest,
    AdminCreateRequest,
    AdminPasswordRequest,
    AdminUpdateRequest,
    ManualPtsRequest,
    PrizeRequest,
    PrizeUpdateRequest,
    RewardCreateRequest,
    RewardUpdateRequest,
    SelfPasswordRequest,
    WheelCreateRequest,
    WheelUpdateRequest,
)
from app.services import admins as admins_service
from app.services import audit, catalog
from app.services import wheel as wheel_service

router = APIRouter(prefix="/api/console", tags=["manage"])


# ============================================================
#  Учётки администраторов
# ============================================================

@router.get("/admins")
def list_admins(
    _: AdminUser = Depends(require_permission(perms.ADMINS_MANAGE)),
    db: Session = Depends(get_db),
) -> dict:
    return {
        "items": [admins_service.payload(a) for a in admins_service.list_admins(db)],
        "available_permissions": [
            {"code": p, "label": perms.LABELS[p], "owner_only": p in perms.OWNER_ONLY}
            for p in perms.ALL_PERMISSIONS
        ],
    }


@router.post("/admins")
def create_admin(
    body: AdminCreateRequest,
    admin: AdminUser = Depends(require_permission(perms.ADMINS_MANAGE)),
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = admins_service.create(
            db,
            username=body.username,
            password=body.password,
            display_name=body.display_name,
            permissions=body.permissions,
            club_id=body.club_id,
        )
    except admins_service.AdminError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit.log(db, admin.username, "admin_create", target_type="admin", target_id=row.username,
              detail={"permissions": body.permissions})
    db.commit()
    return admins_service.payload(row)


@router.patch("/admins/{admin_id}")
def update_admin(
    admin_id: int,
    body: AdminUpdateRequest,
    admin: AdminUser = Depends(require_permission(perms.ADMINS_MANAGE)),
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = admins_service.update(
            db,
            admin_id,
            display_name=body.display_name,
            permissions=body.permissions,
            is_active=body.is_active,
            club_id=body.club_id,
        )
    except admins_service.AdminError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit.log(db, admin.username, "admin_update", target_type="admin", target_id=row.username,
              detail={"permissions": body.permissions, "is_active": body.is_active})
    db.commit()
    return admins_service.payload(row)


@router.post("/admins/{admin_id}/password")
def set_admin_password(
    admin_id: int,
    body: AdminPasswordRequest,
    admin: AdminUser = Depends(require_permission(perms.ADMINS_MANAGE)),
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = admins_service.set_password(db, admin_id, body.password)
    except admins_service.AdminError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit.log(db, admin.username, "admin_password_reset", target_type="admin", target_id=row.username)
    db.commit()
    return {"ok": True}


@router.delete("/admins/{admin_id}")
def delete_admin(
    admin_id: int,
    admin: AdminUser = Depends(require_permission(perms.ADMINS_MANAGE)),
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = admins_service.get(db, admin_id)
        username = row.username
        admins_service.delete(db, admin_id)
    except admins_service.AdminError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit.log(db, admin.username, "admin_delete", target_type="admin", target_id=username)
    db.commit()
    return {"ok": True}


@router.post("/auth/password")
def change_own_password(
    body: SelfPasswordRequest,
    admin: AdminUser = Depends(current_admin_user),
    db: Session = Depends(get_db),
) -> dict:
    """Смену своего пароля не ограничиваем правами — она нужна каждому."""
    if not verify_password(body.current_password, admin.password_hash):
        raise HTTPException(status_code=400, detail="Текущий пароль неверен")
    try:
        admins_service.set_password(db, admin.id, body.new_password)
    except admins_service.AdminError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit.log(db, admin.username, "password_change_self")
    db.commit()
    return {"ok": True}


# ============================================================
#  Достижения
# ============================================================

@router.get("/achievements")
def list_achievements(
    _: AdminUser = Depends(require_permission(perms.CATALOG_EDIT)),
    db: Session = Depends(get_db),
) -> dict:
    return {"items": [catalog.achievement_payload(a) for a in catalog.list_achievements(db)]}


@router.patch("/achievements/{achievement_id}")
def update_achievement(
    achievement_id: int,
    body: AchievementUpdateRequest,
    admin: AdminUser = Depends(require_permission(perms.CATALOG_EDIT)),
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = catalog.update_achievement(db, achievement_id, body.model_dump(exclude_unset=True))
    except catalog.CatalogError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit.log(db, admin.username, "achievement_update", target_type="achievement", target_id=row.code,
              detail=body.model_dump(exclude_unset=True))
    db.commit()
    return catalog.achievement_payload(row)


# ============================================================
#  Награды
# ============================================================

@router.get("/rewards")
def list_rewards(
    _: AdminUser = Depends(require_permission(perms.CATALOG_EDIT)),
    db: Session = Depends(get_db),
) -> dict:
    return {"items": [catalog.reward_payload(r) for r in catalog.list_rewards(db)]}


@router.post("/rewards")
def create_reward(
    body: RewardCreateRequest,
    admin: AdminUser = Depends(require_permission(perms.CATALOG_EDIT)),
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = catalog.create_reward(db, body.model_dump())
    except catalog.CatalogError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit.log(db, admin.username, "reward_create", target_type="reward", target_id=row.code)
    db.commit()
    return catalog.reward_payload(row)


@router.patch("/rewards/{reward_id}")
def update_reward(
    reward_id: int,
    body: RewardUpdateRequest,
    admin: AdminUser = Depends(require_permission(perms.CATALOG_EDIT)),
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = catalog.update_reward(db, reward_id, body.model_dump(exclude_unset=True))
    except catalog.CatalogError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit.log(db, admin.username, "reward_update", target_type="reward", target_id=row.code,
              detail=body.model_dump(exclude_unset=True))
    db.commit()
    return catalog.reward_payload(row)


@router.delete("/rewards/{reward_id}")
def delete_reward(
    reward_id: int,
    admin: AdminUser = Depends(require_permission(perms.CATALOG_EDIT)),
    db: Session = Depends(get_db),
) -> dict:
    try:
        catalog.delete_reward(db, reward_id)
    except catalog.CatalogError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit.log(db, admin.username, "reward_disable", target_type="reward", target_id=str(reward_id))
    db.commit()
    return {"ok": True}


# ============================================================
#  ЛУДЛЕНТА
# ============================================================

@router.get("/wheels")
def list_wheels(
    _: AdminUser = Depends(require_permission(perms.CATALOG_EDIT)),
    db: Session = Depends(get_db),
) -> dict:
    return {
        "items": [catalog.wheel_payload(db, w) for w in catalog.list_wheels(db)],
        "rewards": [
            catalog.reward_payload(r) for r in catalog.list_rewards(db) if r.is_active
        ],
    }


@router.get("/wheels/{wheel_id}/stats")
def wheel_stats(
    wheel_id: int,
    _: AdminUser = Depends(require_permission(perms.CATALOG_EDIT)),
    db: Session = Depends(get_db),
) -> dict:
    return wheel_service.stats(db, wheel_id)


@router.post("/wheels")
def create_wheel(
    body: WheelCreateRequest,
    admin: AdminUser = Depends(require_permission(perms.CATALOG_EDIT)),
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = catalog.create_wheel(db, body.model_dump())
    except catalog.CatalogError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit.log(db, admin.username, "wheel_create", target_type="wheel", target_id=row.code)
    db.commit()
    return catalog.wheel_payload(db, row)


@router.patch("/wheels/{wheel_id}")
def update_wheel(
    wheel_id: int,
    body: WheelUpdateRequest,
    admin: AdminUser = Depends(require_permission(perms.CATALOG_EDIT)),
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = catalog.update_wheel(db, wheel_id, body.model_dump(exclude_unset=True))
    except catalog.CatalogError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit.log(db, admin.username, "wheel_update", target_type="wheel", target_id=row.code,
              detail=body.model_dump(exclude_unset=True))
    db.commit()
    return catalog.wheel_payload(db, row)


@router.post("/wheels/{wheel_id}/prizes")
def create_prize(
    wheel_id: int,
    body: PrizeRequest,
    admin: AdminUser = Depends(require_permission(perms.CATALOG_EDIT)),
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = catalog.create_prize(db, wheel_id, body.model_dump())
    except catalog.CatalogError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit.log(db, admin.username, "prize_create", target_type="prize", target_id=str(row.id),
              detail={"wheel_id": wheel_id, "title": row.title, "weight": row.weight})
    db.commit()
    return {"ok": True, "id": row.id}


@router.patch("/prizes/{prize_id}")
def update_prize(
    prize_id: int,
    body: PrizeUpdateRequest,
    admin: AdminUser = Depends(require_permission(perms.CATALOG_EDIT)),
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = catalog.update_prize(db, prize_id, body.model_dump(exclude_unset=True))
    except catalog.CatalogError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit.log(db, admin.username, "prize_update", target_type="prize", target_id=str(row.id),
              detail=body.model_dump(exclude_unset=True))
    db.commit()
    return {"ok": True}


@router.delete("/prizes/{prize_id}")
def delete_prize(
    prize_id: int,
    admin: AdminUser = Depends(require_permission(perms.CATALOG_EDIT)),
    db: Session = Depends(get_db),
) -> dict:
    try:
        catalog.delete_prize(db, prize_id)
    except catalog.CatalogError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit.log(db, admin.username, "prize_disable", target_type="prize", target_id=str(prize_id))
    db.commit()
    return {"ok": True}


# ============================================================
#  Ручное начисление PTS
# ============================================================

@router.post("/users/{telegram_id}/pts")
def grant_pts(
    telegram_id: int,
    body: ManualPtsRequest,
    admin: AdminUser = Depends(require_permission(perms.PTS_GRANT)),
    db: Session = Depends(get_db),
) -> dict:
    """Начисление и списание одной ручкой через тело запроса: отрицательная сумма — списание."""
    from app.services import achievements, pts

    user = db.query(User).filter(User.telegram_id == telegram_id).one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    try:
        if body.amount > 0:
            pts.credit(db, user, body.amount, reason=TxReason.MANUAL, comment=body.comment)
        else:
            pts.debit(db, user, -body.amount, reason=TxReason.MANUAL, comment=body.comment)

    except pts.InsufficientFunds as exc:
        raise HTTPException(status_code=400, detail=f"Недостаточно PTS: {exc}") from exc

    achievements.on_pts_changed(db, user)
    audit.log(db, admin.username, "pts_grant", target_type="user", target_id=str(telegram_id),
              detail={"amount": body.amount, "comment": body.comment})
    db.commit()
    return {"ok": True, "balance": user.pts_balance}



# ============================================================
#  Google Sheets
# ============================================================

@router.get("/sheets/status")
def sheets_status(
    _: AdminUser = Depends(require_permission(perms.CODES_APPROVE)),
    db: Session = Depends(get_db),
) -> dict:
    """Состояние интеграции и сколько строк ждёт выгрузки."""
    from app.services import sheets

    return {
        "configured": sheets.is_configured(),
        "worksheet": get_settings().google_sheet_worksheet,
        "autoexport": get_settings().google_autoexport,
        "pending": len(sheets.pending_export(db)),
    }


@router.post("/sheets/check")
def sheets_check(
    _: AdminUser = Depends(require_permission(perms.CODES_APPROVE)),
) -> dict:
    """Проверка доступа: открывается ли таблица сервисным аккаунтом."""
    from app.services import sheets

    try:
        return sheets.check_connection()
    except sheets.SheetsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sheets/export")
def sheets_export(
    admin: AdminUser = Depends(require_permission(perms.CODES_APPROVE)),
    db: Session = Depends(get_db),
) -> dict:
    """Выгрузить всё подтверждённое, что ещё не уехало в таблицу."""
    from app.services import sheets

    try:
        result = sheets.export_pending(db)
    except sheets.SheetsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if result.get("exported"):
        audit.log(db, admin.username, "sheets_export", detail={"count": result["exported"]})
        db.commit()
    return result
