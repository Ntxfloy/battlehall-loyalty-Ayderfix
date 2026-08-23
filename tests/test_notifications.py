"""Тесты очереди уведомлений (app/services/notifications.py) и её связки
с кодами наград.

Проверяем три вещи, которые легко сломать рефакторингом:
1. Дедупликацию по dedup_key (гость не должен получать одно и то же дважды).
2. Постановку событий при подтверждении и сгорании кода.
3. Бэкофф и переход в failed после исчерпания попыток.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import (
    NotificationKind,
    NotificationOutbox,
    NotificationStatus,
    RedemptionStatus,
    Reward,
    RewardKind,
    RewardRedemption,
)
from app.services import notifications, rewards


def _reward(db) -> Reward:
    reward = Reward(
        code="cash_500",
        kind=RewardKind.CASH,
        title="500 рублей на счёт",
        cost_pts=500,
        payout_value=500,
        payout_unit="RUB",
    )
    db.add(reward)
    db.flush()
    return reward


def _redemption(
    db,
    user,
    reward,
    *,
    status: str,
    code: str,
    pts_spent: int = 500,
    expires_in_hours: int = 24,
) -> RewardRedemption:
    now = datetime.now(timezone.utc)
    row = RewardRedemption(
        user_id=user.id,
        reward_id=reward.id,
        code=code,
        status=status,
        pts_spent=pts_spent,
        reward_title=reward.title,
        payout_value=reward.payout_value,
        payout_unit=reward.payout_unit,
        created_at=now,
        expires_at=now + timedelta(hours=expires_in_hours),
        used_at=now if status == RedemptionStatus.SUBMITTED else None,
        used_by="staff" if status == RedemptionStatus.SUBMITTED else None,
    )
    db.add(row)
    db.flush()
    return row


def _outbox(db) -> list[NotificationOutbox]:
    return list(
        db.execute(select(NotificationOutbox).order_by(NotificationOutbox.id)).scalars()
    )


def test_dedup_key_blocks_duplicates(db, user):
    first = notifications.enqueue(
        db,
        user_id=user.id,
        kind=NotificationKind.CODE_APPROVED,
        text="первое",
        dedup_key="code_approved:ABCD1234",
    )
    second = notifications.enqueue(
        db,
        user_id=user.id,
        kind=NotificationKind.CODE_APPROVED,
        text="второе",
        dedup_key="code_approved:ABCD1234",
    )

    assert first is not None
    assert second is None
    rows = _outbox(db)
    assert len(rows) == 1
    assert rows[0].text == "первое"
    assert rows[0].telegram_id == user.telegram_id


def test_enqueue_without_dedup_key_allows_repeats(db, user):
    notifications.enqueue(db, user_id=user.id, kind=NotificationKind.PTS_GRANTED, text="+100 PTS")
    notifications.enqueue(db, user_id=user.id, kind=NotificationKind.PTS_GRANTED, text="+100 PTS")
    assert len(_outbox(db)) == 2


def test_approve_code_enqueues_notification(db, user):
    reward = _reward(db)
    _redemption(db, user, reward, status=RedemptionStatus.SUBMITTED, code="AAAA1111")

    rewards.approve_code(db, "AAAA1111", admin="owner")

    rows = _outbox(db)
    assert len(rows) == 1
    assert rows[0].kind == NotificationKind.CODE_APPROVED
    assert rows[0].dedup_key == "code_approved:AAAA1111"
    assert "AAAA1111" in rows[0].text


def test_expired_code_notification_mentions_refund(db, user):
    reward = _reward(db)
    row = _redemption(db, user, reward, status=RedemptionStatus.PENDING, code="BBBB2222")
    # Срок уже вышел — регламентный прогон должен погасить код и вернуть PTS.
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.add(row)
    db.flush()

    expired = rewards.expire_due(db)

    assert expired == 1
    rows = _outbox(db)
    assert len(rows) == 1
    assert rows[0].kind == NotificationKind.CODE_EXPIRED
    assert rows[0].dedup_key == "code_expired:BBBB2222"
    assert "500 PTS" in rows[0].text


def test_expire_due_twice_does_not_duplicate_notification(db, user):
    reward = _reward(db)
    row = _redemption(db, user, reward, status=RedemptionStatus.PENDING, code="CCCC3333")
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.add(row)
    db.flush()

    rewards.expire_due(db)
    rewards.expire_due(db)

    assert len(_outbox(db)) == 1


def test_due_skips_future_and_leased_rows(db, user):
    ready = notifications.enqueue(db, user_id=user.id, kind=NotificationKind.PTS_GRANTED, text="готово")
    later = notifications.enqueue(db, user_id=user.id, kind=NotificationKind.PTS_GRANTED, text="позже")
    later.next_attempt_at = datetime.now(timezone.utc) + timedelta(minutes=30)
    db.add(later)
    db.flush()

    due_ids = [row.id for row in notifications.due(db)]
    assert due_ids == [ready.id]

    # После аренды строка не должна второй раз попасть в выборку.
    notifications.lease(db, [ready.id])
    db.flush()
    assert notifications.due(db) == []


def test_mark_sent_sets_status(db, user):
    row = notifications.enqueue(db, user_id=user.id, kind=NotificationKind.PTS_GRANTED, text="+50 PTS")
    changed = notifications.mark_sent(db, [row.id])
    db.expire_all()

    assert changed == 1
    fresh = db.get(NotificationOutbox, row.id)
    assert fresh.status == NotificationStatus.SENT
    assert fresh.sent_at is not None


def test_backoff_grows_then_row_fails(db, user):
    row = notifications.enqueue(db, user_id=user.id, kind=NotificationKind.PTS_GRANTED, text="+50 PTS")
    now = datetime.now(timezone.utc)

    notifications.mark_failed(db, row.id, "TelegramNetworkError: timeout", now=now)
    assert row.status == NotificationStatus.PENDING
    assert row.attempts == 1
    first_delay = row.next_attempt_at

    notifications.mark_failed(db, row.id, "TelegramNetworkError: timeout", now=now)
    assert row.attempts == 2
    # Вторая пауза длиннее первой — именно в этом смысл бэкоффа.
    assert row.next_attempt_at > first_delay

    for _ in range(notifications.MAX_ATTEMPTS - 2):
        notifications.mark_failed(db, row.id, "TelegramForbiddenError: bot was blocked", now=now)

    assert row.attempts == notifications.MAX_ATTEMPTS
    assert row.status == NotificationStatus.FAILED
    # Строка больше не берётся в работу и не крутит очередь вечно.
    assert notifications.due(db) == []
    assert "blocked" in row.last_error


def test_stats_counts_by_status(db, user):
    sent = notifications.enqueue(db, user_id=user.id, kind=NotificationKind.PTS_GRANTED, text="1")
    notifications.enqueue(db, user_id=user.id, kind=NotificationKind.PTS_GRANTED, text="2")
    notifications.mark_sent(db, [sent.id])

    counts = notifications.stats(db)
    assert counts[NotificationStatus.SENT] == 1
    assert counts[NotificationStatus.PENDING] == 1
    assert counts[NotificationStatus.FAILED] == 0
