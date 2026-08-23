"""Выгрузка подтверждённых компенсаций в Google Sheets.

Доступ — через сервисный аккаунт Google, а не через личный OAuth: серверу
нужно писать в таблицу без участия человека, а токен пользователя пришлось бы
периодически обновлять руками. Владелец просто открывает таблице доступ
на редактирование для e-mail сервисного аккаунта.

Интеграция необязательная: без настроек всё остальное работает как раньше,
а выгрузка просто отдаёт JSON для ручного переноса.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import RedemptionStatus, RewardRedemption, User
from app.periods import ensure_utc

logger = logging.getLogger(__name__)

# Права: только таблицы, ничего лишнего у сервисного аккаунта не просим.
SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)

HEADER = (
    "Код",
    "Дата подтверждения",
    "Telegram ID",
    "Ник",
    "Телефон",
    "Награда",
    "Номинал",
    "Ед.",
    "Списано PTS",
    "Внёс на стойке",
    "Подтвердил",
    "Источник",
)


class SheetsError(Exception):
    pass


def is_configured() -> bool:
    s = get_settings()
    return bool(s.google_sheet_id and s.google_credentials_file)


def _worksheet():
    """Открывает лист. Импорты внутри: без настроенной интеграции
    google-библиотеки не должны требоваться для запуска приложения."""
    if not is_configured():
        raise SheetsError("Google Sheets не настроен: нужны GOOGLE_SHEET_ID и GOOGLE_CREDENTIALS_FILE")

    import pathlib
    s = get_settings()

    key_path = pathlib.Path(s.google_credentials_file)
    if not key_path.exists():
        raise SheetsError(f"Файл ключа не найден: {key_path}")

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError as exc:
        raise SheetsError("Не установлены gspread и google-auth") from exc

    try:
        credentials = Credentials.from_service_account_file(str(key_path), scopes=list(SCOPES))
        client = gspread.authorize(credentials)
        spreadsheet = client.open_by_key(s.google_sheet_id)
    except Exception as exc:
        raise SheetsError(f"Не удалось открыть таблицу: {exc}") from exc

    title = s.google_sheet_worksheet
    try:
        return spreadsheet.worksheet(title)
    except Exception:
        # Листа с таким названием нет — заводим его вместе с шапкой,
        # чтобы владельцу не пришлось готовить таблицу руками.
        worksheet = spreadsheet.add_worksheet(title=title, rows=1000, cols=len(HEADER))
        worksheet.append_row(list(HEADER), value_input_option="USER_ENTERED")
        return worksheet


def check_connection() -> dict:
    """Проверка настроек из панели: открывается ли таблица и виден ли лист."""
    worksheet = _worksheet()
    return {
        "ok": True,
        "spreadsheet": worksheet.spreadsheet.title,
        "worksheet": worksheet.title,
        "rows": worksheet.row_count,
    }


def _row_for(db: Session, row: RewardRedemption) -> list:
    guest = db.get(User, row.user_id)
    approved = ensure_utc(row.approved_at) if row.approved_at else None
    return [
        row.code,
        approved.strftime("%d.%m.%Y %H:%M") if approved else "",
        guest.telegram_id if guest else "",
        f"@{guest.username}" if guest and guest.username else "",
        guest.phone if guest and guest.phone else "",
        row.reward_title,
        float(row.payout_value),
        "₽" if row.payout_unit == "RUB" else "мес.",
        row.pts_spent,
        row.used_by or "",
        row.approved_by or "",
        "ЛУДЛЕНТА" if row.source == "wheel" else "каталог",
    ]


def pending_export(db: Session, limit: int = 500) -> list[RewardRedemption]:
    """Подтверждённые, но ещё не выгруженные строки."""
    return list(
        db.execute(
            select(RewardRedemption)
            .where(
                RewardRedemption.status == RedemptionStatus.APPROVED,
                RewardRedemption.exported_at.is_(None),
            )
            .order_by(RewardRedemption.approved_at)
            .limit(limit)
        ).scalars()
    )


def export_pending(db: Session) -> dict:
    """Дописывает подтверждённые компенсации в таблицу.

    `exported_at` проставляется только после успешной записи: если Google
    недоступен, строки останутся в очереди и уедут при следующей попытке.
    Дублей не будет — повторно берутся только строки без отметки.
    """
    rows = pending_export(db)
    if not rows:
        return {"exported": 0, "skipped": "нечего выгружать"}

    worksheet = _worksheet()
    payload = [_row_for(db, row) for row in rows]

    try:
        worksheet.append_rows(payload, value_input_option="USER_ENTERED")
    except Exception as exc:
        raise SheetsError(f"Не удалось записать в таблицу: {exc}") from exc

    now = datetime.now(timezone.utc)
    for row in rows:
        row.exported_at = now
        db.add(row)
    db.flush()

    logger.info("В Google Sheets выгружено строк: %s", len(rows))
    return {"exported": len(rows), "codes": [r.code for r in rows]}


def export_one(db: Session, redemption: RewardRedemption) -> bool:
    """Выгрузка одной строки сразу после подтверждения.

    Ошибку наружу не пробрасываем: подтверждение кода не должно падать
    из-за недоступного Google — строка просто останется в очереди.
    """
    if not is_configured() or redemption.exported_at is not None:
        return False
    try:
        worksheet = _worksheet()
        worksheet.append_rows([_row_for(db, redemption)], value_input_option="USER_ENTERED")
    except Exception as exc:
        logger.warning("Строка %s не уехала в таблицу, останется в очереди: %s", redemption.code, exc)
        return False

    redemption.exported_at = datetime.now(timezone.utc)
    db.add(redemption)
    db.flush()
    return True

