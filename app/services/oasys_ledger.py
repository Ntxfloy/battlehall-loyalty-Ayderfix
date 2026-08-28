"""Журнал движения денег на счёте гостя внутри самой OASys (вебхук ещё не
подключён — см. README «Что нужно от команды OASys»).

Это НЕ PTS и не бизнес-логика лояльности — просто сверяемый журнал, чтобы
на стойке можно было ответить на спор «гость говорит, что пополнил счёт,
а у нас пусто». Заменяет пуллинг GET /method/admin/operations/history
из app/services/oasys_live.py.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Club, OasysBalanceOperation
from app.periods import ensure_utc
from app.services.sessions import resolve_user


class BalanceOperationIngestError(Exception):
    pass


class UserNotLinked(BalanceOperationIngestError):
    """Операция пришла на гостя, который ещё не привязал Telegram к программе."""


def _find(db: Session, club_id: int, external_operation_id: str) -> OasysBalanceOperation | None:
    return db.execute(
        select(OasysBalanceOperation).where(
            OasysBalanceOperation.club_id == club_id,
            OasysBalanceOperation.external_operation_id == external_operation_id,
        )
    ).scalar_one_or_none()


def ingest(db: Session, club: Club, payload) -> tuple[OasysBalanceOperation, bool]:
    """Идемпотентно: повторный вебхук с тем же operation_id в рамках клуба
    ничего не меняет — это только журнал, дублей быть не должно."""
    existing = _find(db, club.id, payload.operation_id)
    if existing is not None:
        return existing, False

    user = resolve_user(
        db,
        telegram_id=payload.telegram_id,
        phone=payload.phone,
        oasys_client_id=payload.client_id,
    )
    if user is None:
        raise UserNotLinked("гость не привязан к программе лояльности")

    row = OasysBalanceOperation(
        user_id=user.id,
        club_id=club.id,
        external_operation_id=payload.operation_id,
        operation_type=payload.operation_type,
        amount=payload.amount,
        payment_method=payload.payment_method,
        occurred_at=ensure_utc(payload.created_at),
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = _find(db, club.id, payload.operation_id)
        if existing is None:
            raise BalanceOperationIngestError(f"Не удалось сохранить операцию {payload.operation_id}") from None
        return existing, False

    return row, True
