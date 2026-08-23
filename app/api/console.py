"""API для веб-панели администратора. Отдельный префикс /api/console —
чтобы не путать с /api/admin/* (те работают и по сервисному токену, эти
только по куке сессии панели, см. app/admin_auth.py: current_admin_user)."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import permissions as perms
from app.admin_auth import (
    SESSION_COOKIE,
    create_session_value,
    current_admin_user,
    forbid_in_production,
    require_permission,
    verify_password,
)
from app.config import get_settings, is_local_env
from app.db import get_db
from app.loyalty import group_for_hours
from app.models import AdminUser, Club, User
from app.periods import iso
from app.schemas import (
    AdminLoginRequest,
    ClubCreateRequest,
    ClubUpdateRequest,
    TestSessionEndRequest,
    TestSessionStartRequest,
)
from app.services import achievements, audit, clubs as clubs_service, oasys_live, pts, rewards, sessions
from app.zones import zone_title

router = APIRouter(prefix="/api/console", tags=["console"])
settings = get_settings()


# --- вход ---

@router.post("/auth/login")
def login(body: AdminLoginRequest, response: Response, db: Session = Depends(get_db)) -> dict:
    admin = db.execute(
        select(AdminUser).where(AdminUser.username == body.username)
    ).scalar_one_or_none()

    if admin is None or not admin.is_active or not verify_password(body.password, admin.password_hash):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    admin.last_login_at = datetime.now(timezone.utc)
    db.add(admin)
    audit.log(db, admin.username, "login")
    db.commit()

    response.set_cookie(
        SESSION_COOKIE,
        create_session_value(admin.id),
        httponly=True,
        samesite="lax",
        secure=not is_local_env(),
        max_age=settings.admin_session_ttl_hours * 3600,
    )
    return {"ok": True, "username": admin.username, "display_name": admin.display_name}


@router.post("/auth/logout")
def logout(response: Response, admin: AdminUser = Depends(current_admin_user), db: Session = Depends(get_db)) -> dict:
    audit.log(db, admin.username, "logout")
    db.commit()
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/auth/me")
def me(admin: AdminUser = Depends(current_admin_user)) -> dict:
    """Отдаём и права: панель по ним прячет разделы. Это только удобство —
    доступ всё равно проверяется на каждой ручке отдельно."""
    return {
        "username": admin.username,
        "display_name": admin.display_name,
        "role": admin.role,
        "permissions": sorted(perms.granted(admin)),
    }


# --- клубы ---

@router.get("/clubs")
def list_clubs(_: AdminUser = Depends(require_permission(perms.CLUBS_VIEW)), db: Session = Depends(get_db)) -> dict:
    from app.config import is_placeholder_secret

    return {
        "items": [
            {
                "id": c.id,
                "slug": c.slug,
                "name": c.name,
                "is_active": c.is_active,
                "webhook_configured": not is_placeholder_secret(c.oasys_webhook_token),
            }
            for c in clubs_service.list_clubs(db)
        ]
    }


@router.post("/clubs")
def create_club(
    body: ClubCreateRequest,
    admin: AdminUser = Depends(require_permission(perms.CLUBS_EDIT)),
    db: Session = Depends(get_db),
) -> dict:
    try:
        club = clubs_service.create(db, body.slug, body.name)
    except clubs_service.ClubError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.log(db, admin.username, "club_create", target_type="club", target_id=club.slug)
    db.commit()
    return {"id": club.id, "slug": club.slug, "name": club.name, "webhook_token": club.oasys_webhook_token}


@router.patch("/clubs/{club_id}")
def update_club(
    club_id: int,
    body: ClubUpdateRequest,
    admin: AdminUser = Depends(require_permission(perms.CLUBS_EDIT)),
    db: Session = Depends(get_db),
) -> dict:
    club = db.get(Club, club_id)
    if club is None:
        raise HTTPException(status_code=404, detail="Клуб не найден")
    clubs_service.update(db, club, name=body.name, is_active=body.is_active)
    audit.log(db, admin.username, "club_update", target_type="club", target_id=club.slug,
              detail={"name": body.name, "is_active": body.is_active})
    db.commit()
    return {"ok": True}


@router.post("/clubs/{club_id}/rotate-token")
def rotate_token(
    club_id: int,
    admin: AdminUser = Depends(require_permission(perms.CLUBS_EDIT)),
    db: Session = Depends(get_db),
) -> dict:
    club = db.get(Club, club_id)
    if club is None:
        raise HTTPException(status_code=404, detail="Клуб не найден")
    clubs_service.rotate_token(db, club)
    audit.log(db, admin.username, "club_rotate_token", target_type="club", target_id=club.slug)
    db.commit()
    return {"webhook_token": club.oasys_webhook_token}


# --- пользователи ---

@router.get("/users")
def list_users(
    q: str | None = Query(default=None, description="поиск по username/имени/телефону/telegram_id"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=200),
    _: AdminUser = Depends(require_permission(perms.USERS_VIEW)),
    db: Session = Depends(get_db),
) -> dict:
    from sqlalchemy import func, or_

    filters = []
    if q:
        needle = f"%{q.strip()}%"
        filters = [User.username.ilike(needle), User.first_name.ilike(needle), User.phone.ilike(needle)]
        if q.strip().isdigit():
            filters.append(User.telegram_id == int(q.strip()))

    stmt = select(User)
    count_stmt = select(func.count(User.id))
    if filters:
        stmt = stmt.where(or_(*filters))
        count_stmt = count_stmt.where(or_(*filters))

    total_count = db.execute(count_stmt).scalar_one()

    rows = list(
        db.execute(
            stmt.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        ).scalars()
    )

    items = []
    for u in rows:
        year = datetime.now(timezone.utc).year
        hours = sessions.year_stats(db, u.id, year)["hours"]
        items.append(
            {
                "telegram_id": u.telegram_id,
                "username": u.username,
                "first_name": u.first_name,
                "phone": u.phone,
                "balance": u.pts_balance,
                "group_title": f"{group_for_hours(hours).level}. {group_for_hours(hours).title}",
                "created_at": iso(u.created_at),
                "referral_code": u.referral_code,
            }
        )

    return {"items": items, "total": total_count, "page": page, "page_size": page_size}


@router.get("/users/{telegram_id}")
def user_detail(
    telegram_id: int,
    _: AdminUser = Depends(require_permission(perms.USERS_VIEW)),
    db: Session = Depends(get_db),
) -> dict:

    user = db.execute(select(User).where(User.telegram_id == telegram_id)).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    year = datetime.now(timezone.utc).year
    stats = sessions.year_stats(db, user.id, year)
    group = group_for_hours(stats["hours"])

    visits = sessions.visit_history(db, user.id, limit=100)
    pts_history = pts.history(db, user.id, limit=100)
    redemptions = rewards.history(db, user, limit=50)
    overview = achievements.overview(db, user)

    return {
        "user": {
            "telegram_id": user.telegram_id,
            "username": user.username,
            "first_name": user.first_name,
            "phone": user.phone,
            "balance": user.pts_balance,
            "referral_code": user.referral_code,
            "referred_by_id": user.referred_by_id,
            "referral_credited": user.referral_credited,
            "created_at": iso(user.created_at),
        },
        "group": f"{group.level}. {group.title}",
        "stats": stats,
        "visits": [
            {
                "started_at": iso(v.started_at),
                "ended_at": iso(v.ended_at),
                "pc_number": v.pc_number,
                "zone": zone_title(v.zone_code),
                "minutes": v.duration_minutes,
                "is_closed": v.is_closed,
            }
            for v in visits
        ],
        "pts_history": [
            {
                "amount": t.amount,
                "balance_after": t.balance_after,
                "reason": t.reason,
                "comment": t.comment,
                "created_at": iso(t.created_at),
            }
            for t in pts_history
        ],
        "redemptions": [
            {
                "code": r.code,
                "status": r.status,
                "title": r.reward_title,
                "pts_spent": r.pts_spent,
                "created_at": iso(r.created_at),
                "used_at": iso(r.used_at),
            }
            for r in redemptions
        ],
        "achievements": overview,
    }


# --- OASys, живые данные (не вебхуки, см. app/services/oasys_live.py) ---

@router.get("/oasys/map")
def oasys_map(
    club_id: int | None = Query(default=None, description="для карты зон конкретного клуба, если есть переопределения"),
    _: AdminUser = Depends(require_permission(perms.OASYS_VIEW)),
    db: Session = Depends(get_db),
) -> dict:
    overrides = None
    if club_id is not None:
        club = db.get(Club, club_id)
        overrides = club.pc_zone_overrides if club else None
    try:
        return oasys_live.live_map(overrides)
    except oasys_live.OasysLiveError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/oasys/cashier-stats")
def oasys_cashier_stats(_: AdminUser = Depends(require_permission(perms.OASYS_VIEW))) -> dict:
    try:
        return oasys_live.cashier_stats()
    except oasys_live.OasysLiveError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/oasys/discounts")
def oasys_discounts(_: AdminUser = Depends(require_permission(perms.OASYS_VIEW))) -> dict:
    try:
        return {
            "club_discounts": oasys_live.club_discounts(),
            "promo_codes": oasys_live.promo_codes(),
        }
    except oasys_live.OasysLiveError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# --- журнал действий ---

@router.get("/logs")
def logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    action: str | None = None,
    _: AdminUser = Depends(require_permission(perms.LOGS_VIEW)),
    db: Session = Depends(get_db),
) -> dict:
    rows = audit.recent(db, limit=page_size, offset=(page - 1) * page_size, action=action)
    return {
        "items": [
            {
                "admin": r.admin_username,
                "action": r.action,
                "target_type": r.target_type,
                "target_id": r.target_id,
                "detail": r.detail,
                "created_at": iso(r.created_at),
            }
            for r in rows
        ]
    }


# --- отчётность по клубам ---

@router.get("/reports")
def reports(
    date_from: str | None = Query(default=None, description="YYYY-MM-DD, игровой день"),
    date_to: str | None = Query(default=None),
    _: AdminUser = Depends(require_permission(perms.REPORTS_VIEW)),
    db: Session = Depends(get_db),
) -> dict:
    return clubs_service.network_summary(db, date_from, date_to)


# --- тестовые запросы (симуляция вебхуков OASys прямо из панели) ---

test_router = APIRouter(
    prefix="/api/console/test",
    tags=["console-test"],
    dependencies=[Depends(forbid_in_production)],
)


def _ensure_test_user(db: Session, telegram_id: int) -> None:
    """Настоящий вебхук OASys молча пропускает гостя, которого ещё нет
    в программе, — это верно для продакшена. Но для ручной проверки в панели
    удобнее завести гостя на лету, чем сначала гонять его через мини-апп."""
    exists = db.execute(select(User.id).where(User.telegram_id == telegram_id)).first()
    if exists is None:
        from app.services import referrals

        db.add(User(telegram_id=telegram_id, first_name="Тест", referral_code=referrals.generate_code(db)))
        db.flush()


@test_router.post("/session-start")
def test_session_start(
    body: TestSessionStartRequest,
    admin: AdminUser = Depends(require_permission(perms.TEST_TOOLS)),
    db: Session = Depends(get_db),
) -> dict:
    club = clubs_service.get_by_slug(db, body.club_slug)
    if club is None:
        raise HTTPException(status_code=404, detail="Клуб не найден")

    _ensure_test_user(db, body.telegram_id)

    from app.schemas import SessionStartPayload

    payload = SessionStartPayload(
        session_id=body.session_id or f"test-{int(datetime.now(timezone.utc).timestamp())}",
        pc_number=body.pc_number,
        started_at=body.started_at or datetime.now(timezone.utc),
        telegram_id=body.telegram_id,
    )
    try:
        row, created = sessions.start_session(db, club, payload)
    except sessions.SessionIngestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit.log(db, admin.username, "test_session_start", target_type="session", target_id=row.oasys_session_id,
              detail={"club": club.slug, "telegram_id": body.telegram_id, "pc": body.pc_number})
    db.commit()
    return {"ok": True, "session_id": row.oasys_session_id, "created": created, "zone": row.zone_code}


@test_router.post("/session-end")
def test_session_end(
    body: TestSessionEndRequest,
    admin: AdminUser = Depends(require_permission(perms.TEST_TOOLS)),
    db: Session = Depends(get_db),
) -> dict:
    club = clubs_service.get_by_slug(db, body.club_slug)
    if club is None:
        raise HTTPException(status_code=404, detail="Клуб не найден")

    _ensure_test_user(db, body.telegram_id)

    from app.schemas import SessionEndPayload

    payload = SessionEndPayload(
        session_id=body.session_id,
        pc_number=body.pc_number or 1,
        started_at=body.started_at or datetime.now(timezone.utc),
        telegram_id=body.telegram_id,
        duration_minutes=body.duration_minutes,
    )
    try:
        row, closed = sessions.end_session(db, club, payload)
    except sessions.SessionIngestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit.log(db, admin.username, "test_session_end", target_type="session", target_id=row.oasys_session_id,
              detail={"club": club.slug, "minutes": row.duration_minutes})
    db.commit()
    return {"ok": True, "session_id": row.oasys_session_id, "closed": closed, "minutes": row.duration_minutes}
