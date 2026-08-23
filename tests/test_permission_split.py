"""Разделение прав: выгрузка и обслуживание больше не висят на codes.approve."""

import pytest

from app import permissions as perms
from app.admin_auth import create_session_value
from app.models import AdminRole, AdminUser
from app.services import admins as admins_service
import seed


def _login_as(client, admin: AdminUser) -> None:
    client.cookies.set("bh_admin_session", create_session_value(admin.id))


def test_staff_with_export_can_see_sheets_but_cannot_approve(client, db):
    staff = admins_service.create(
        db,
        "exporter",
        "password123",
        permissions=[perms.REPORTS_EXPORT, perms.CODES_VIEW],
    )
    db.commit()
    _login_as(client, staff)

    assert client.get("/api/console/sheets/status").status_code == 200
    assert client.post("/api/console/desk/approve", json={"code": "ANYCODE"}).status_code == 403
    client.cookies.clear()


def test_staff_without_export_cannot_open_sheets(client, db):
    staff = admins_service.create(
        db,
        "deskonly",
        "password123",
        permissions=[perms.CODES_VIEW, perms.CODES_SUBMIT],
    )
    db.commit()
    _login_as(client, staff)

    assert client.get("/api/console/sheets/status").status_code == 403
    assert client.post("/api/console/sheets/export").status_code == 403
    client.cookies.clear()


def test_clubs_view_lists_but_does_not_create(client, db):
    staff = admins_service.create(
        db,
        "clubviewer",
        "password123",
        permissions=[perms.CLUBS_VIEW],
    )
    db.commit()
    _login_as(client, staff)

    listed = client.get("/api/console/clubs")
    assert listed.status_code == 200
    assert "webhook_token" not in listed.json()["items"][0]
    assert client.post("/api/console/clubs", json={"slug": "other", "name": "Other"}).status_code == 403
    client.cookies.clear()


def test_maintenance_run_can_expire_without_approve(client, db):
    staff = admins_service.create(
        db,
        "janitor",
        "password123",
        permissions=[perms.MAINTENANCE_RUN],
    )
    db.commit()
    _login_as(client, staff)

    resp = client.post("/api/admin/maintenance/expire-codes")
    assert resp.status_code == 200
    assert "expired" in resp.json()
    assert client.post("/api/console/desk/approve", json={"code": "ANYCODE"}).status_code == 403
    client.cookies.clear()


def test_seed_refuses_weak_owner_password_in_production(db, settings_patch):
    db.query(AdminUser).delete()
    db.commit()
    settings_patch(admin_default_password="change-me-now", app_env="production")

    with pytest.raises(RuntimeError, match="ADMIN_DEFAULT_PASSWORD"):
        seed.seed_default_admin(db)

    assert db.query(AdminUser).count() == 0


def test_new_permissions_are_assignable_to_staff():
    clean = perms.validate_assignable(
        [perms.CLUBS_VIEW, perms.REPORTS_EXPORT, perms.MAINTENANCE_RUN, perms.CODES_APPROVE]
    )
    assert perms.CLUBS_VIEW in clean
    assert perms.REPORTS_EXPORT in clean
    assert perms.MAINTENANCE_RUN in clean
    assert perms.CODES_APPROVE not in clean
