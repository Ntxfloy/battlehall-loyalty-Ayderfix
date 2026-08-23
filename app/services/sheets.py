"""Выгрузка подтверждённых компенсаций в Google Sheets.

Доступ — через сервисный аккаунт Google, а не через личный OAuth: серверу
нужно писать в таблицу без участия человека, а токен пользователя пришлось бы
периодически обновлять руками. Владелец просто открывает таблице доступ
на редактирование для e-mail сервисного аккаунта.

Интеграция необязательная: без настроек всё остальное работает как раньше,
а выгрузка просто отдаёт JSON для ручного переноса.

Очередь выгрузки — сами подтверждённые строки с пустым exported_at.
Право записать строку в таблицу выигрывается условным UPDATE: второй воркер
не отправит ту же компенсацию повторно.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import RedemptionStatus, RewardRedemption, User
from app.periods import ensure_utc

logger = logging.getLogger(__name__)

# Права: только таблицы, ничего лишнего у сервисного аккаунта не просим.
SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)

# RAW: иначе Google превратит «01.02» и коды в даты/числа.
VALUE_INPUT_OPTION = "RAW"

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
        worksheet.append_row(list(HEADER), value_input_option=VALUE_INPUT_OPTION)
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


def _claim_export(db: Session, row: RewardRedemption, now: datetime) -> bool:
    """Забирает строку в очередь выгрузки. Второй воркер получает rowcount=0."""
    result = db.execute(
        update(RewardRedemption)
        .where(
            RewardRedemption.id == row.id,
            RewardRedemption.status == RedemptionStatus.APPROVED,
            RewardRedemption.exported_at.is_(None),
        )
        .values(exported_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 1:
        db.expire(row, ["exported_at"])
        return True
    return False


def _unclaim_export(db: Session, row: RewardRedemption, claimed_at: datetime) -> None:
    db.execute(
        update(RewardRedemption)
        .where(
            RewardRedemption.id == row.id,
            RewardRedemption.exported_at == claimed_at,
        )
        .values(exported_at=None)
        .execution_options(synchronize_session=False)
    )
    db.expire(row, ["exported_at"])


def export_pending(db: Session) -> dict:
    """Дописывает подтверждённые компенсации в таблицу.

    Строки сначала забираются условным UPDATE, и только потом уходят в Google.
    Если запись в таблицу падает, отметки снимаются — очередь не теряется.
    """
    rows = pending_export(db)
    if not rows:
        return {"exported": 0, "skipped": "нечего выгружать"}

    now = datetime.now(timezone.utc)
    claimed: list[RewardRedemption] = []
    for row in rows:
        if _claim_export(db, row, now):
            claimed.append(row)

    if not claimed:
        return {"exported": 0, "skipped": "уже забрано другим процессом"}

    try:
        worksheet = _worksheet()
        worksheet.append_rows(
            [_row_for(db, row) for row in claimed],
            value_input_option=VALUE_INPUT_OPTION,
        )
    except Exception as exc:
        for row in claimed:
            _unclaim_export(db, row, now)
        raise SheetsError(f"Не удалось записать в таблицу: {exc}") from exc

    logger.info("В Google Sheets выгружено строк: %s", len(claimed))
    return {"exported": len(claimed), "codes": [r.code for r in claimed]}


def export_one(db: Session, redemption: RewardRedemption) -> bool:
    """Выгрузка одной строки сразу после подтверждения.

    Сетевой сбой пробрасывается как SheetsError: подтверждение кода уже
    закоммичено отдельно, а строка должна остаться в очереди.
    """
    if not is_configured() or redemption.exported_at is not None:
        return False

    now = datetime.now(timezone.utc)
    if not _claim_export(db, redemption, now):
        return False

    try:
        worksheet = _worksheet()
        worksheet.append_rows([_row_for(db, redemption)], value_input_option=VALUE_INPUT_OPTION)
    except Exception as exc:
        _unclaim_export(db, redemption, now)
        logger.warning("Строка %s не уехала в таблицу, останется в очереди: %s", redemption.code, exc)
        raise SheetsError(f"Не удалось записать в таблицу: {exc}") from exc

    return True
