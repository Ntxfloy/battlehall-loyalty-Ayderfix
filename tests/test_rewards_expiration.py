"""Тесты идемпотентности погашения наград, атомарных переходов статусов и отсутствия мутаций на чтении."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update
from sqlalchemy.sql.dml import Update

from app.models import RedemptionStatus, Reward, RewardRedemption, TxReason, User
from app.services import pts, rewards


def test_expire_due_skips_row_taken_by_concurrent_worker(db, user, monkeypatch):
    """Строку погасил другой процесс между нашим SELECT и UPDATE.
    Возврат PTS должен сделать он, а не мы."""
    pts.credit(db, user, 5000, comment="тест")
    db.commit()

    reward = db.query(Reward).filter(Reward.code == "cash_300").one()
    row = rewards.redeem(db, user, reward.id)
    db.commit()

    balance_after_redeem = user.pts_balance
    row_id = row.id

    row.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.add(row)
    db.commit()

    original_execute = db.execute
    hijacked = {"done": False}

    def execute_with_race(statement, *args, **kwargs):
        # Первый UPDATE от expire_due перехватываем: имитируем, что строку
        # уже погасил и закоммитил конкурент, и только потом отдаём наш запрос в базу.
        if not hijacked["done"] and isinstance(statement, Update):
            hijacked["done"] = True
            original_execute(
                update(RewardRedemption)
                .where(RewardRedemption.id == row_id)
                .values(
                    status=RedemptionStatus.EXPIRED,
                    expired_at=datetime.now(timezone.utc),
                    refunded_at=datetime.now(timezone.utc),
                )
                .execution_options(synchronize_session=False)
            )
            db.commit()
        return original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db, "execute", execute_with_race)

    expired = rewards.expire_due(db)
    db.commit()

    monkeypatch.undo()

    assert expired == 0  # право не выиграно
    db.refresh(user)
    assert user.pts_balance == balance_after_redeem  # возврат начислен конкурентом, а не нами
    refunds = [
        t for t in pts.history(db, user.id, limit=100)
        if t.reason == TxReason.REWARD_REFUND
    ]
    assert len(refunds) == 0


def test_expired_and_refunded_code_cannot_be_submitted(db, user):
    """Код погашен с возвратом PTS — внести его на стойке уже нельзя."""
    pts.credit(db, user, 5000, comment="тест")
    db.commit()

    reward = db.query(Reward).filter(Reward.code == "cash_300").one()
    row = rewards.redeem(db, user, reward.id)
    db.commit()
    code = row.code
    balance_after_redeem = user.pts_balance

    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.add(row)
    db.commit()

    assert rewards.expire_due(db) == 1
    db.commit()

    db.refresh(user)
    assert user.pts_balance == balance_after_redeem + reward.cost_pts  # возврат прошёл

    with pytest.raises(rewards.RewardError) as exc:
        rewards.use_code(db, code, "desk1")
    assert "истёк" in str(exc.value)

    db.refresh(row)
    assert row.status == RedemptionStatus.EXPIRED  # не submitted


def test_use_code_reports_expiry_for_unswept_code(db, user):
    """Код просрочен, но cron ещё не прогнал: сообщение должно быть про срок."""
    pts.credit(db, user, 5000, comment="тест")
    db.commit()
    reward = db.query(Reward).filter(Reward.code == "cash_300").one()
    row = rewards.redeem(db, user, reward.id)
    db.commit()
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.add(row)
    db.commit()

    with pytest.raises(rewards.RewardError) as exc:
        rewards.use_code(db, row.code, "desk1")
    assert "истёк" in str(exc.value)
    db.refresh(row)
    assert row.status == RedemptionStatus.PENDING  # чтение не мутирует


def test_second_approve_is_rejected_with_message(db, user):
    """Последовательный двойной аппрув одного кода: второй вызов падает с ошибкой."""
    pts.credit(db, user, 5000, comment="тест")
    db.commit()

    reward = db.query(Reward).filter(Reward.code == "cash_300").one()
    row = rewards.redeem(db, user, reward.id)
    db.commit()
    code = row.code

    rewards.use_code(db, code, "desk1")
    db.refresh(row)
    assert row.status == RedemptionStatus.SUBMITTED

    # Первый approve проходит успешно
    approved_row = rewards.approve_code(db, code, "owner1")
    assert approved_row.status == RedemptionStatus.APPROVED

    # Повторный approve падает с понятной ошибкой
    with pytest.raises(rewards.RewardError) as exc:
        rewards.approve_code(db, code, "owner2")
    assert "подтверждён" in str(exc.value)


def test_approve_code_loses_race_to_concurrent_owner(db, user, monkeypatch):
    """Гонка двух администраторов на аппруве: второй администратор не выигрывает UPDATE."""
    pts.credit(db, user, 5000, comment="тест")
    db.commit()

    reward = db.query(Reward).filter(Reward.code == "cash_300").one()
    row = rewards.redeem(db, user, reward.id)
    db.commit()
    code = row.code

    rewards.use_code(db, code, "desk1")
    db.refresh(row)
    assert row.status == RedemptionStatus.SUBMITTED

    original_execute = db.execute
    hijacked = {"done": False}

    def execute_with_approve_race(statement, *args, **kwargs):
        # Перед UPDATE имитируем, что другой админ уже подтвердил код в базе
        if not hijacked["done"] and isinstance(statement, Update):
            hijacked["done"] = True
            original_execute(
                update(RewardRedemption)
                .where(RewardRedemption.id == row.id)
                .values(
                    status=RedemptionStatus.APPROVED,
                    approved_at=datetime.now(timezone.utc),
                    approved_by="owner_first",
                )
                .execution_options(synchronize_session=False)
            )
            db.commit()
        return original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db, "execute", execute_with_approve_race)

    with pytest.raises(rewards.RewardError) as exc:
        rewards.approve_code(db, code, "owner_second")

    monkeypatch.undo()
    assert "подтверждён" in str(exc.value)


def test_reject_code_loses_race_to_concurrent_approve(db, user, monkeypatch):
    """Гонка между reject и approve: если код уже подтверждён, отклонение отвергается."""
    pts.credit(db, user, 5000, comment="тест")
    db.commit()

    reward = db.query(Reward).filter(Reward.code == "cash_300").one()
    row = rewards.redeem(db, user, reward.id)
    db.commit()
    code = row.code

    rewards.use_code(db, code, "desk1")
    db.refresh(row)
    assert row.status == RedemptionStatus.SUBMITTED

    original_execute = db.execute
    hijacked = {"done": False}

    def execute_with_reject_race(statement, *args, **kwargs):
        if not hijacked["done"] and isinstance(statement, Update):
            hijacked["done"] = True
            original_execute(
                update(RewardRedemption)
                .where(RewardRedemption.id == row.id)
                .values(
                    status=RedemptionStatus.APPROVED,
                    approved_at=datetime.now(timezone.utc),
                    approved_by="owner1",
                )
                .execution_options(synchronize_session=False)
            )
            db.commit()
        return original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db, "execute", execute_with_reject_race)

    with pytest.raises(rewards.RewardError) as exc:
        rewards.reject_code(db, code, "owner2")

    monkeypatch.undo()
    assert "подтверждён" in str(exc.value)


def test_expire_due_does_not_refund_when_disabled(db, user, settings_patch):
    """Если refund_pts_on_expire=False, статус меняется на EXPIRED и ставится expired_at, но PTS не возвращаются."""
    settings_patch(refund_pts_on_expire=False)

    pts.credit(db, user, 5000, comment="тест")
    db.commit()


    reward = db.query(Reward).filter(Reward.code == "cash_300").one()
    row = rewards.redeem(db, user, reward.id)
    balance_after_redeem = user.pts_balance

    row.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.add(row)
    db.commit()

    count = rewards.expire_due(db)
    db.commit()
    assert count == 1

    db.refresh(user)
    assert user.pts_balance == balance_after_redeem
    refunds = [t for t in pts.history(db, user.id) if t.reason == TxReason.REWARD_REFUND]
    assert len(refunds) == 0

    db.refresh(row)
    assert row.status == RedemptionStatus.EXPIRED
    assert row.expired_at is not None
    assert row.refunded_at is None


def test_expire_due_ignores_approved_and_submitted_codes(db, user):
    """Коды в статусах SUBMITTED и APPROVED не гасятся, даже если expires_at в прошлом."""
    now = datetime.now(timezone.utc)
    reward = db.query(Reward).filter(Reward.code == "cash_300").one()

    submitted_row = RewardRedemption(
        user_id=user.id,
        reward_id=reward.id,
        code="SUBM1234",
        status=RedemptionStatus.SUBMITTED,
        pts_spent=1000,
        reward_title=reward.title,
        payout_value=reward.payout_value,
        payout_unit=reward.payout_unit,
        created_at=now - timedelta(days=2),
        expires_at=now - timedelta(days=1),
    )
    approved_row = RewardRedemption(
        user_id=user.id,
        reward_id=reward.id,
        code="APPR1234",
        status=RedemptionStatus.APPROVED,
        pts_spent=1000,
        reward_title=reward.title,
        payout_value=reward.payout_value,
        payout_unit=reward.payout_unit,
        created_at=now - timedelta(days=2),
        expires_at=now - timedelta(days=1),
    )
    db.add_all([submitted_row, approved_row])
    db.commit()

    expired = rewards.expire_due(db)
    db.commit()
    assert expired == 0

    db.refresh(submitted_row)
    db.refresh(approved_row)
    assert submitted_row.status == RedemptionStatus.SUBMITTED
    assert approved_row.status == RedemptionStatus.APPROVED


def test_expire_due_does_not_commit(db, user):
    """expire_due не должен вызывать commit(): после db.rollback() изменения отменяются."""
    pts.credit(db, user, 5000, comment="тест")
    db.commit()

    reward = db.query(Reward).filter(Reward.code == "cash_300").one()
    row = rewards.redeem(db, user, reward.id)
    db.commit()

    row.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.add(row)
    db.commit()

    count = rewards.expire_due(db)
    assert count == 1
    # Откатываем транзакцию
    db.rollback()

    db.refresh(row)
    assert row.status == RedemptionStatus.PENDING
    assert row.refunded_at is None
    assert row.expired_at is None


def test_reading_rewards_does_not_expire_anything(client, db, user):
    """GET /api/rewards и GET /api/redemptions не мутируют базу."""
    from app.auth import current_user
    from app.main import app

    app.dependency_overrides[current_user] = lambda: user

    pts.credit(db, user, 5000, comment="тест")
    db.commit()

    reward = db.query(Reward).filter(Reward.code == "cash_300").one()
    row = rewards.redeem(db, user, reward.id)
    db.commit()

    row.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.add(row)
    db.commit()

    # Делаем GET-запросы наград
    resp1 = client.get("/api/rewards")
    assert resp1.status_code == 200
    assert resp1.json()["active_code"] is None  # просроченный не активен

    resp2 = client.get("/api/redemptions")
    assert resp2.status_code == 200
    item = resp2.json()["items"][0]
    assert item["status"] == "expired"  # клиент видит expired динамически

    # Проверяем, что в базе строка всё ещё PENDING, и возврата не было
    db.refresh(row)
    assert row.status == RedemptionStatus.PENDING
    assert row.refunded_at is None

    refunds = [t for t in pts.history(db, user.id) if t.reason == TxReason.REWARD_REFUND]
    assert len(refunds) == 0

    app.dependency_overrides.clear()
