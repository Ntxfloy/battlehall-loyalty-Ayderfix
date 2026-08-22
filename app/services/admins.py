"""Учётки администраторов: заводит и правит только владелец."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import permissions as perms
from app.admin_auth import hash_password
from app.models import AdminRole, AdminUser

MIN_PASSWORD_LENGTH = 8


class AdminError(Exception):
    pass


def list_admins(db: Session) -> list[AdminUser]:
    return list(db.execute(select(AdminUser).order_by(AdminUser.id)).scalars())


def get(db: Session, admin_id: int) -> AdminUser:
    row = db.get(AdminUser, admin_id)
    if row is None:
        raise AdminError("Учётка не найдена")
    return row


def create(
    db: Session,
    username: str,
    password: str,
    display_name: str = "",
    permissions: list[str] | None = None,
    club_id: int | None = None,
) -> AdminUser:
    username = username.strip().lower()
    if not username:
        raise AdminError("Логин не может быть пустым")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AdminError(f"Пароль короче {MIN_PASSWORD_LENGTH} символов")

    exists = db.execute(select(AdminUser.id).where(AdminUser.username == username)).first()
    if exists:
        raise AdminError(f"Учётка «{username}» уже существует")

    row = AdminUser(
        username=username,
        password_hash=hash_password(password),
        display_name=display_name or username,
        role=AdminRole.STAFF,   # владелец в системе один, новые — всегда сотрудники
        permissions=perms.dump(permissions if permissions is not None else perms.DEFAULT_STAFF),
        club_id=club_id,
    )
    db.add(row)
    db.commit()
    return row


def update(
    db: Session,
    admin_id: int,
    display_name: str | None = None,
    permissions: list[str] | None = None,
    is_active: bool | None = None,
    club_id: int | None = None,
) -> AdminUser:
    row = get(db, admin_id)
    if row.role == AdminRole.OWNER and (permissions is not None or is_active is False):
        # Права владельца неизменяемы, и отключить его нельзя — иначе панель
        # можно оставить вообще без администратора.
        raise AdminError("Учётку владельца нельзя ограничить или отключить")

    if display_name is not None:
        row.display_name = display_name
    if permissions is not None:
        row.permissions = perms.dump(permissions)
    if is_active is not None:
        row.is_active = is_active
    if club_id is not None:
        row.club_id = club_id or None

    db.add(row)
    db.commit()
    return row


def set_password(db: Session, admin_id: int, password: str) -> AdminUser:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AdminError(f"Пароль короче {MIN_PASSWORD_LENGTH} символов")
    row = get(db, admin_id)
    row.password_hash = hash_password(password)
    db.add(row)
    db.commit()
    return row


def delete(db: Session, admin_id: int) -> None:
    row = get(db, admin_id)
    if row.role == AdminRole.OWNER:
        raise AdminError("Учётку владельца нельзя удалить")
    db.delete(row)
    db.commit()


def payload(row: AdminUser) -> dict:
    from app.periods import iso

    return {
        "id": row.id,
        "username": row.username,
        "display_name": row.display_name,
        "role": row.role,
        "permissions": sorted(perms.granted(row)),
        "club_id": row.club_id,
        "is_active": row.is_active,
        "last_login_at": iso(row.last_login_at),
    }
