from collections import Counter

import pytest
from fastapi.testclient import TestClient

from app import permissions as perms
from app.models import (
    AdminRole,
    PrizeKind,
    RedemptionStatus,
    Reward,
    Wheel,
    WheelPrize,
)
from app.services import admins as admins_service
from app.services import catalog, pts, rewards
from app.services import wheel as wheel_service


@pytest.fixture
def client(db):
    from app.main import app

    return TestClient(app)


def _login(client, username="admin", password="test-password-123"):
    response = client.post("/api/console/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response


@pytest.fixture
def loot(db):
    """Лента с предсказуемым составом: 90% на PTS, 10% на пусто."""
    wheel = Wheel(code="test_loot", title="Тестовая лента", cost_pts=100)
    db.add(wheel)
    db.flush()
    db.add(WheelPrize(wheel_id=wheel.id, title="200 PTS", kind=PrizeKind.PTS, pts_amount=200, weight=90))
    db.add(WheelPrize(wheel_id=wheel.id, title="Пусто", kind=PrizeKind.NOTHING, weight=10))
    db.commit()
    return wheel


# --- механика ленты ---

def test_spin_charges_cost_and_returns_prize(db, user, loot):
    pts.credit(db, user, 1000, comment="тест")
    db.commit()

    result = wheel_service.spin(db, user, loot.id)
    prize = result["spins"][0]["prize"]

    assert result["cost_pts"] == 100
    assert result["count"] == 1
    assert prize["title"] in {"200 PTS", "Пусто"}
    # списали прокрутку, начислили выигрыш (если он был)
    expected = 1000 - 100 + prize["pts_won"]
    assert result["balance"] == expected


def test_spin_without_pts_is_rejected(db, user, loot):
    with pytest.raises(wheel_service.WheelError):
        wheel_service.spin(db, user, loot.id)


def test_invalid_count_is_rejected(db, user, loot):
    pts.credit(db, user, 1000, comment="тест")
    db.commit()
    with pytest.raises(wheel_service.WheelError):
        wheel_service.spin(db, user, loot.id, count=3)


def test_reel_contains_winner_at_declared_index(db, user, loot):
    pts.credit(db, user, 1000, comment="тест")
    db.commit()

    result = wheel_service.spin(db, user, loot.id)
    one = result["spins"][0]
    reel = one["reel"]
    index = one["winning_index"]

    assert 0 <= index < len(reel)
    assert reel[index]["title"] == one["prize"]["title"]


def test_multi_spin_charges_once_and_returns_all_results(db, user, loot):
    pts.credit(db, user, 1000, comment="тест")
    db.commit()

    result = wheel_service.spin(db, user, loot.id, count=5)

    assert result["count"] == 5
    assert result["cost_pts"] == 500
    assert len(result["spins"]) == 5
    won = sum(s["prize"]["pts_won"] for s in result["spins"])
    assert result["balance"] == 1000 - 500 + won
    assert len(wheel_service.history(db, user.id)) == 5


def test_multi_spin_is_all_or_nothing(db, user, loot):
    """500 PTS хватает на 5 прокруток, но не на 10 — вся пачка должна
    отклоняться, а не списываться частично."""
    pts.credit(db, user, 500, comment="тест")
    db.commit()

    with pytest.raises(wheel_service.WheelError):
        wheel_service.spin(db, user, loot.id, count=10)

    db.refresh(user)
    assert user.pts_balance == 500
    assert len(wheel_service.history(db, user.id)) == 0


def test_all_in_spins_as_many_as_balance_allows(db, user, loot):
    pts.credit(db, user, 1050, comment="тест")   # 1050 // 100 = 10 прокруток, 50 останутся
    db.commit()

    result = wheel_service.spin(db, user, loot.id, all_in=True)

    assert result["all_in"] is True
    assert result["count"] == 10
    assert result["cost_pts"] == 1000
    assert len(result["spins"]) == 10


def test_all_in_is_capped(db, user, loot):
    pts.credit(db, user, 10_000, comment="тест")   # хватило бы на 100, но потолок — 20
    db.commit()

    result = wheel_service.spin(db, user, loot.id, all_in=True)

    assert result["count"] == wheel_service.ALL_IN_CAP


def test_all_in_without_enough_for_one_spin_is_rejected(db, user, loot):
    pts.credit(db, user, 50, comment="тест")   # меньше цены одной прокрутки (100)
    db.commit()

    with pytest.raises(wheel_service.WheelError):
        wheel_service.spin(db, user, loot.id, all_in=True)

    db.refresh(user)
    assert user.pts_balance == 50


def test_weights_shape_the_distribution(db, user, loot):
    """90/10 не обязан дать ровно 90 из 100, но перекос должен быть явным."""
    pts.credit(db, user, 100_000, comment="тест")
    db.commit()

    outcomes = Counter()
    for _ in range(200):
        result = wheel_service.spin(db, user, loot.id)
        outcomes[result["spins"][0]["prize"]["title"]] += 1

    assert outcomes["200 PTS"] > outcomes["Пусто"] * 3


def test_zero_weight_prize_never_drops(db, user):
    wheel = Wheel(code="w0", title="Лента", cost_pts=10)
    db.add(wheel)
    db.flush()
    db.add(WheelPrize(wheel_id=wheel.id, title="Всегда", kind=PrizeKind.PTS, pts_amount=1, weight=5))
    db.add(WheelPrize(wheel_id=wheel.id, title="Никогда", kind=PrizeKind.PTS, pts_amount=999, weight=0))
    db.commit()

    pts.credit(db, user, 5000, comment="тест")
    db.commit()

    titles = {wheel_service.spin(db, user, wheel.id)["spins"][0]["prize"]["title"] for _ in range(60)}
    assert titles == {"Всегда"}


def test_reward_prize_issues_a_code(db, user):
    reward = db.query(Reward).filter(Reward.code == "cash_300").one()
    wheel = Wheel(code="w_reward", title="Лента с наградой", cost_pts=50)
    db.add(wheel)
    db.flush()
    db.add(
        WheelPrize(
            wheel_id=wheel.id, title="300 ₽", kind=PrizeKind.REWARD, reward_id=reward.id, weight=1
        )
    )
    db.commit()

    pts.credit(db, user, 500, comment="тест")
    db.commit()

    result = wheel_service.spin(db, user, wheel.id)
    assert result["spins"][0]["prize"]["code"] is not None
    # приз уже оплачен прокруткой — второй раз за него не списываем
    assert result["balance"] == 500 - 50


def test_prize_pointing_at_deleted_reward_refunds_the_spin(db, user):
    """Награду могли удалить из каталога уже после настройки ленты —
    гость не должен остаться ни с чем."""
    wheel = Wheel(code="w_broken", title="Битая лента", cost_pts=70)
    db.add(wheel)
    db.flush()
    db.add(WheelPrize(wheel_id=wheel.id, title="Фантом", kind=PrizeKind.REWARD, reward_id=9999, weight=1))
    db.commit()

    pts.credit(db, user, 300, comment="тест")
    db.commit()

    result = wheel_service.spin(db, user, wheel.id)
    assert result["balance"] == 300      # списали и вернули
    assert result["spins"][0]["prize"]["code"] is None


def test_chance_is_computed_from_weights(db, loot):
    prizes = wheel_service.prizes_of(db, loot.id)
    by_title = {p.title: wheel_service.chance_of(p, prizes) for p in prizes}
    assert by_title["200 PTS"] == 90.0
    assert by_title["Пусто"] == 10.0


def test_spin_history_is_recorded(db, user, loot):
    pts.credit(db, user, 1000, comment="тест")
    db.commit()
    wheel_service.spin(db, user, loot.id)
    wheel_service.spin(db, user, loot.id)

    assert len(wheel_service.history(db, user.id)) == 2


# --- права ---

def test_owner_has_every_permission(db):
    from app.models import AdminUser

    owner = db.query(AdminUser).filter(AdminUser.role == AdminRole.OWNER).one()
    assert perms.granted(owner) == set(perms.ALL_PERMISSIONS)


def test_staff_only_gets_what_owner_granted(db):
    staff = admins_service.create(db, "desk1", "password123", permissions=[perms.CODES_VIEW])
    assert perms.granted(staff) == {perms.CODES_VIEW}
    assert perms.has(staff, perms.PTS_GRANT) is False


def test_owner_only_permissions_cannot_be_granted_to_staff(db):
    """Смысл разделения ролей в том, что сотрудник не аппрувит сам себя."""
    staff = admins_service.create(
        db, "desk2", "password123", permissions=[perms.CODES_SUBMIT, perms.CODES_APPROVE, perms.ADMINS_MANAGE]
    )
    granted = perms.granted(staff)
    assert perms.CODES_SUBMIT in granted
    assert perms.CODES_APPROVE not in granted
    assert perms.ADMINS_MANAGE not in granted


def test_owner_account_cannot_be_disabled(db):
    from app.models import AdminUser

    owner = db.query(AdminUser).filter(AdminUser.role == AdminRole.OWNER).one()
    with pytest.raises(admins_service.AdminError):
        admins_service.update(db, owner.id, is_active=False)


def test_short_password_rejected(db):
    with pytest.raises(admins_service.AdminError):
        admins_service.create(db, "weak", "123")


def test_duplicate_admin_username_rejected(db):
    admins_service.create(db, "dupe", "password123")
    with pytest.raises(admins_service.AdminError):
        admins_service.create(db, "dupe", "password123")


# --- права через API ---

def test_staff_cannot_reach_owner_only_endpoints(client, db):
    admins_service.create(db, "deskuser", "password123", permissions=[perms.CODES_VIEW, perms.CODES_SUBMIT])
    _login(client, "deskuser", "password123")

    assert client.get("/api/console/admins").status_code == 403
    assert client.get("/api/console/rewards").status_code == 403
    assert client.get("/api/console/wheels").status_code == 403


def test_staff_can_reach_its_own_desk(client, db, user):
    admins_service.create(db, "deskuser", "password123", permissions=[perms.CODES_VIEW, perms.CODES_SUBMIT])
    _login(client, "deskuser", "password123")

    assert client.get("/api/console/desk/search?q=нет-такого").status_code == 200


def test_staff_cannot_approve(client, db, user):
    admins_service.create(db, "deskuser", "password123", permissions=[perms.CODES_VIEW, perms.CODES_SUBMIT])
    _login(client, "deskuser", "password123")

    assert client.post("/api/console/desk/approve", json={"code": "ANYCODE"}).status_code == 403


# --- поток «внёс код -> аппрув владельца» ---

def _make_code(db, user) -> str:
    pts.credit(db, user, 5000, comment="тест")
    db.commit()
    reward = db.query(Reward).filter(Reward.code == "cash_300").one()
    return rewards.redeem(db, user, reward.id).code


def test_submitted_code_waits_for_approval(client, db, user):
    code = _make_code(db, user)
    admins_service.create(db, "deskuser", "password123", permissions=[perms.CODES_VIEW, perms.CODES_SUBMIT])

    _login(client, "deskuser", "password123")
    submitted = client.post("/api/console/desk/submit", json={"code": code})
    assert submitted.status_code == 200
    assert submitted.json()["status"] == RedemptionStatus.SUBMITTED

    # до аппрува строка не попадает в выгрузку для таблицы
    _login(client)
    export = client.get("/api/admin/redemptions").json()["items"]
    assert code not in {row["code"] for row in export}

    queue = client.get("/api/console/desk/queue").json()["items"]
    assert code in {row["code"] for row in queue}


def test_owner_approval_puts_code_into_export(client, db, user):
    code = _make_code(db, user)
    _login(client)

    client.post("/api/console/desk/submit", json={"code": code})
    approved = client.post("/api/console/desk/approve", json={"code": code})
    assert approved.status_code == 200
    assert approved.json()["status"] == RedemptionStatus.APPROVED

    export = client.get("/api/admin/redemptions").json()["items"]
    assert code in {row["code"] for row in export}


def test_same_code_cannot_be_submitted_twice(client, db, user):
    code = _make_code(db, user)
    _login(client)

    assert client.post("/api/console/desk/submit", json={"code": code}).status_code == 200
    assert client.post("/api/console/desk/submit", json={"code": code}).status_code == 400


def test_rejected_code_returns_to_the_guest(client, db, user):
    code = _make_code(db, user)
    _login(client)

    client.post("/api/console/desk/submit", json={"code": code})
    rejected = client.post("/api/console/desk/reject", json={"code": code})
    assert rejected.json()["status"] == RedemptionStatus.PENDING

    # код снова можно внести
    assert client.post("/api/console/desk/submit", json={"code": code}).status_code == 200


def test_desk_search_finds_code_by_guest_phone(client, db, user):
    user.phone = "79990001122"
    db.add(user)
    db.commit()
    code = _make_code(db, user)

    _login(client)
    found = client.get("/api/console/desk/search?q=79990001122").json()["items"]
    assert code in {row["code"] for row in found}
    assert found[0]["guest"]["phone"] == "79990001122"


# --- редактирование каталога ---

def test_owner_can_edit_achievement_target(client, db):
    _login(client)
    items = client.get("/api/console/achievements").json()["items"]
    target = next(a for a in items if a["code"] == "month_days_15")

    response = client.patch(
        f"/api/console/achievements/{target['id']}",
        json={"title": "Новое имя", "target": 20, "reward_pts": 999},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Новое имя"
    assert body["target"] == 20
    assert body["reward_pts"] == 999


def test_reward_crud_round_trip(client, db):
    _login(client)
    created = client.post(
        "/api/console/rewards",
        json={"code": "cash_50", "title": "50 ₽", "cost_pts": 500, "payout_value": 50},
    )
    assert created.status_code == 200
    reward_id = created.json()["id"]

    updated = client.patch(f"/api/console/rewards/{reward_id}", json={"cost_pts": 600})
    assert updated.json()["cost_pts"] == 600

    client.delete(f"/api/console/rewards/{reward_id}")
    after = {r["id"]: r for r in client.get("/api/console/rewards").json()["items"]}
    assert after[reward_id]["is_active"] is False   # выключена, но не стёрта


def test_wheel_and_prize_crud(client, db):
    _login(client)
    wheel = client.post(
        "/api/console/wheels",
        json={"code": "new_loot", "title": "Новая лента", "cost_pts": 200},
    ).json()

    prize = client.post(
        f"/api/console/wheels/{wheel['id']}/prizes",
        json={"title": "500 PTS", "kind": "pts", "pts_amount": 500, "weight": 3},
    )
    assert prize.status_code == 200

    listed = client.get("/api/console/wheels").json()["items"]
    created = next(w for w in listed if w["code"] == "new_loot")
    assert len(created["prizes"]) == 1
    assert created["prizes"][0]["chance"] == 100.0


def test_pts_prize_without_amount_is_rejected(client, db):
    _login(client)
    wheel = client.post(
        "/api/console/wheels", json={"code": "l2", "title": "Лента", "cost_pts": 100}
    ).json()

    response = client.post(
        f"/api/console/wheels/{wheel['id']}/prizes",
        json={"title": "Пустой приз", "kind": "pts", "pts_amount": 0, "weight": 1},
    )
    assert response.status_code == 400


def test_free_wheel_is_rejected(client, db):
    _login(client)
    response = client.post(
        "/api/console/wheels", json={"code": "free", "title": "Бесплатно", "cost_pts": 0}
    )
    assert response.status_code == 422   # отсекается схемой: cost_pts > 0


def test_manual_pts_grant_and_deduction(client, db, user):
    _login(client)
    client.post(f"/api/console/users/{user.telegram_id}/pts?amount=500&comment=бонус")
    db.refresh(user)
    assert user.pts_balance == 500

    client.post(f"/api/console/users/{user.telegram_id}/pts?amount=-200&comment=правка")
    db.refresh(user)
    assert user.pts_balance == 300


def test_cannot_deduct_more_than_balance(client, db, user):
    _login(client)
    response = client.post(f"/api/console/users/{user.telegram_id}/pts?amount=-100&comment=минус")
    assert response.status_code == 400
