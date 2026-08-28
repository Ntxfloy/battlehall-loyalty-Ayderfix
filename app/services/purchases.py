"""Приём покупок пакетов часов из OASys (вебхук ещё не подключён — см.
README «Что нужно от команды OASys»). sku матчится на ачивку через
SKU_ACHIEVEMENTS — неизвестный sku просто не даёт прогресса, сама покупка
всё равно сохраняется для отчётности.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Club, Purchase
from app.services import achievements
from app.services.sessions import resolve_user

# Финальные значения sku подтвердит OASys — здесь то, что предложили им сами.
SKU_ACHIEVEMENTS = {
    "pack_3h": "week_pack_3h",
    "pack_5h": "week_pack_5h",
}


class PurchaseIngestError(Exception):
    pass


class UserNotLinked(PurchaseIngestError):
    """Покупка пришла на гостя, который ещё не привязал Telegram к программе."""


def _find(db: Session, club_id: int, external_purchase_id: str) -> Purchase | None:
    return db.execute(
        select(Purchase).where(
            Purchase.club_id == club_id,
            Purchase.external_purchase_id == external_purchase_id,
        )
    ).scalar_one_or_none()


def ingest(db: Session, club: Club, payload) -> tuple[Purchase, bool]:
    """Идемпотентно: повторный вебхук с тем же purchase_id в рамках клуба
    ничего не меняет и не начисляет прогресс повторно."""
    existing = _find(db, club.id, payload.purchase_id)
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

    row = Purchase(
        user_id=user.id,
        club_id=club.id,
        external_purchase_id=payload.purchase_id,
        sku=payload.sku,
        amount=payload.amount,
        purchased_at=payload.purchased_at,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = _find(db, club.id, payload.purchase_id)
        if existing is None:
            raise PurchaseIngestError(f"Не удалось сохранить покупку {payload.purchase_id}") from None
        return existing, False

    code = SKU_ACHIEVEMENTS.get(payload.sku)
    if code:
        achievements.increment(db, user, code, 1)

    return row, True
