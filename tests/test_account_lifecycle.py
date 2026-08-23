"""Регрессии жизненного цикла администраторов и телефонной привязки."""

import pytest

from app.admin_auth import create_session_value
from app.models import User
from app.services import admins as admins_service, referrals
from bot.main import BotUserError, _save_phone


def _user(db, telegram_id: int, phone: str | None = None) -> User:
    row = User(
        telegram_id=telegram_id,
        phone=phone,
        first_name="Тест",
        referral_code=referrals.generate_code(db),
    )
    db.add(row)
    db.flush()
    return row


def test_admin_delete_is_soft_and_revokes_cookie(client, db):
    staff = admins_service.create(db, "former", "password123")
    staff_id = staff.id
    db.commit()

    client.cookies.set("bh_admin_session", create_session_value(staff_id))
    assert client.get("/api/console/auth/me").status_code == 200

    admins_service.delete(db, staff_id)
    db.commit()

    preserved = db.get(type(staff), staff_id)
    assert preserved is not None
    assert preserved.is_active is False
    assert preserved.permissions == "[]"
    assert client.get("/api/console/auth/me").status_code == 401


def test_deleted_admin_id_is_not_reused(db):
    first = admins_service.create(db, "first-staff", "password123")
    first_id = first.id
    db.commit()
    admins_service.delete(db, first_id)
    db.commit()

    second = admins_service.create(db, "second-staff", "password123")
    db.commit()

    assert second.id != first_id


def test_bot_phone_conflict_has_human_error(db):
    _user(db, 810001, phone="79990001122")
    newcomer = _user(db, 810002)
    db.commit()

    with pytest.raises(BotUserError, match="уже привязан"):
        _save_phone(db, newcomer, "+7 (999) 000-11-22")

    db.rollback()
    db.refresh(newcomer)
    assert newcomer.phone is None


def test_bot_phone_is_normalized_and_saved(db):
    newcomer = _user(db, 820001)
    db.commit()

    normalized = _save_phone(db, newcomer, "8 (999) 123-45-67")
    db.commit()

    assert normalized == "79991234567"
    assert newcomer.phone == "79991234567"
