"""Очередь уведомлений гостю (outbox).

Почему очередь, а не прямая отправка: токен бота живёт в отдельном процессе
(`python -m bot.main`), а синхронная ручка FastAPI не должна ждать сеть
Telegram и тем более падать из-за неё. Поэтому событие пишется строкой в
`notification_outbox` в той же транзакции, что и само действие: если
транзакция откатится, гость не получит сообщение о том, чего не произошло.
Бот разбирает очередь фоновым воркером.

Повторы защищены `dedup_key`: уникальный индекс не даст поставить два
одинаковых уведомления, даже если регламентный прогон повторится.

Жизненный цикл строки:

    pending --(доставлено)--> sent
           \\--(ошибка, попытки кончились)--> failed

Воркер берёт строку, сдвигая `next_attempt_at` вперёд («аренда»): если процесс
упадёт между взятием и отправкой, строка вернётся в очередь сама, без ручного
вмешательства.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    NotificationKind,
    NotificationOutbox,
    NotificationStatus,
    RewardRedemption,
    User,
)

logger = logging.getLogger(__name__)

# Сколько раз пробуем доставить сообщение, прежде чем признать его потерянным.
MAX_ATTEMPTS = 6
# Пауза перед следующей попыткой, в минутах, по номеру неудачи.
BACKOFF_MINUTES = (1, 5, 15, 60, 180)
# На сколько строка уходит «в работу» к воркеру.
LEASE_SECONDS = 120
# Telegram обрежет сообщение сам, но в базе тоже незачем хранить простыню.
TEXT_MAX_LEN = 3500


def enqueue(
    db: Session,
    *,
    user_id: int,
    kind: str,
    text: str,
    dedup_key: str | None = None,
) -> NotificationOutbox | None:
    """Ставит уведомление в очередь. Возвращает None, если ставить некому
    или такое уведомление уже стоит."""
    user = db.get(User, user_id)
    if user is None or not user.telegram_id:
        logger.warning("Уведомление %s пропущено: у пользователя %s нет telegram_id", kind, user_id)
        return None

    now = datetime.now(timezone.utc)
    row = NotificationOutbox(
        user_id=user.id,
        telegram_id=user.telegram_id,
        kind=kind,
        text=text[:TEXT_MAX_LEN],
        dedup_key=dedup_key,
        status=NotificationStatus.PENDING,
        attempts=0,
        next_attempt_at=now,
        created_at=now,
    )

    # Отправляем накопленные изменения до SAVEPOINT: откат неудачной вставки
    # не должен трогать чужие изменения этой же транзакции.
    db.flush()
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        logger.info("Уведомление %s уже стоит в очереди, повтор пропущен", dedup_key)
        return None
    return row


def try_enqueue(db: Session, **kwargs) -> NotificationOutbox | None:
    """Обёртка для бизнес-логики: уведомление — вещь второстепенная и не имеет
    права уронить подтверждение кода или возврат PTS."""
    try:
        return enqueue(db, **kwargs)
    except Exception:   # noqa: BLE001 — намеренно широкий: очередь не критична
        logger.exception("Не удалось поставить уведомление в очередь: %s", kwargs.get("kind"))
        return None


def due(db: Session, limit: int = 20, now: datetime | None = None) -> list[NotificationOutbox]:
    """Строки, готовые к отправке прямо сейчас."""
    if now is None:
        now = datetime.now(timezone.utc)
    return list(
        db.execute(
            select(NotificationOutbox)
            .where(
                NotificationOutbox.status == NotificationStatus.PENDING,
                NotificationOutbox.next_attempt_at <= now,
            )
            .order_by(NotificationOutbox.next_attempt_at, NotificationOutbox.id)
            .limit(limit)
        ).scalars()
    )


def lease(
    db: Session,
    ids: list[int],
    seconds: int = LEASE_SECONDS,
    now: datetime | None = None,
) -> None:
    """Прячет взятые строки от других воркеров на время отправки."""
    if not ids:
        return
    if now is None:
        now = datetime.now(timezone.utc)
    db.execute(
        update(NotificationOutbox)
        .where(
            NotificationOutbox.id.in_(ids),
            NotificationOutbox.status == NotificationStatus.PENDING,
        )
        .values(next_attempt_at=now + timedelta(seconds=seconds))
        .execution_options(synchronize_session=False)
    )


def mark_sent(db: Session, ids: list[int], now: datetime | None = None) -> int:
    if not ids:
        return 0
    if now is None:
        now = datetime.now(timezone.utc)
    result = db.execute(
        update(NotificationOutbox)
        .where(
            NotificationOutbox.id.in_(ids),
            NotificationOutbox.status == NotificationStatus.PENDING,
        )
        .values(status=NotificationStatus.SENT, sent_at=now, last_error=None)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount or 0


def mark_failed(
    db: Session,
    row_id: int,
    error: str,
    now: datetime | None = None,
) -> NotificationOutbox | None:
    """Считает неудачную попытку и назначает следующую. Когда попытки кончились,
    строка получает статус failed — чтобы очередь не крутила её вечно."""
    if now is None:
        now = datetime.now(timezone.utc)
    row = db.get(NotificationOutbox, row_id)
    if row is None or row.status != NotificationStatus.PENDING:
        return row

    row.attempts += 1
    row.last_error = (error or "")[:500]
    if row.attempts >= MAX_ATTEMPTS:
        row.status = NotificationStatus.FAILED
        logger.error(
            "Уведомление %s (%s) не доставлено за %s попыток: %s",
            row.id,
            row.kind,
            row.attempts,
            row.last_error,
        )
    else:
        delay = BACKOFF_MINUTES[min(row.attempts - 1, len(BACKOFF_MINUTES) - 1)]
        row.next_attempt_at = now + timedelta(minutes=delay)
    db.add(row)
    db.flush()
    return row


def stats(db: Session) -> dict:
    """Короткая сводка для админки и диагностики на стенде."""
    counts = {NotificationStatus.PENDING: 0, NotificationStatus.SENT: 0, NotificationStatus.FAILED: 0}
    for status, total in db.execute(
        select(NotificationOutbox.status, __import__("sqlalchemy").func.count(NotificationOutbox.id)).group_by(
            NotificationOutbox.status
        )
    ):
        counts[status] = int(total or 0)
    return counts


# --- Тексты сообщений -------------------------------------------------------
# Держим в одном месте: так проще править тон и не разъезжаться по сервисам.


def code_approved_text(row: RewardRedemption) -> str:
    return (
        f"Код {row.code} подтверждён.\n"
        f"{row.reward_title} — компенсация уйдёт в ближайшую выплату."
    )


def code_expired_text(row: RewardRedemption, refunded_pts: int = 0) -> str:
    text = f"Код {row.code} сгорел: срок действия истёк."
    if refunded_pts > 0:
        text += f"\nВернули {refunded_pts} PTS на баланс — можно обменять заново."
    return text


def referral_credited_text(min_minutes: int) -> str:
    return (
        f"Друг по твоей ссылке отыграл {min_minutes} минут — приглашение засчитано.\n"
        "Загляни в достижения за наградой."
    )


def pts_granted_text(amount: int, comment: str = "") -> str:
    text = f"Начислено {amount} PTS."
    if comment:
        text += f"\n{comment}"
    return text


# Ключи дедупликации — рядом с текстами, чтобы не разъезжались по сервисам.


def code_approved_key(code: str) -> str:
    return f"{NotificationKind.CODE_APPROVED}:{code}"


def code_expired_key(code: str) -> str:
    return f"{NotificationKind.CODE_EXPIRED}:{code}"


def referral_key(invited_user_id: int) -> str:
    return f"{NotificationKind.REFERRAL_CREDITED}:{invited_user_id}"
