"""Досинхронизация схемы SQLite при старте.

`create_all` умеет только создавать отсутствующие таблицы — добавленную
в модель колонку он в существующей таблице не заведёт, и приложение падает
на «no such column». Пока в проекте нет Alembic, этот модуль закрывает разрыв
для SQLite: сравнивает колонки модели с фактическими и добавляет недостающие.

Ограничения намеренные: только ADD COLUMN. Переименование, смена типа и
удаление колонок здесь не делаются — такие изменения требуют настоящей
миграции с переносом данных.

ДЛЯ ПРОДА ЭТОГО НЕДОСТАТОЧНО. Перед запуском на Postgres нужен Alembic.
"""

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.db import Base

logger = logging.getLogger(__name__)


def _sql_default(column) -> str | None:
    """SQLite требует константу в DEFAULT при ADD COLUMN для NOT NULL."""
    default = column.default
    if default is None or not getattr(default, "is_scalar", False):
        return None

    value = default.arg
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return None


def sync_sqlite_columns(engine: Engine) -> list[str]:
    """Добавляет недостающие колонки. Возвращает список выполненных изменений."""
    if engine.dialect.name != "sqlite":
        return []

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    applied: list[str] = []

    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue   # такую таблицу создаст create_all

            present = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue

                ddl = f'ALTER TABLE {table.name} ADD COLUMN {column.name} {column.type.compile(engine.dialect)}'
                default = _sql_default(column)
                if default is not None:
                    ddl += f" DEFAULT {default}"
                elif not column.nullable:
                    # NOT NULL без константного значения по умолчанию SQLite
                    # к непустой таблице не добавит — заводим колонку
                    # допускающей NULL, чтобы не терять уже записанные строки.
                    logger.warning(
                        "%s.%s объявлена NOT NULL без значения по умолчанию — "
                        "добавляю как NULL-совместимую",
                        table.name,
                        column.name,
                    )

                connection.execute(text(ddl))
                applied.append(f"{table.name}.{column.name}")

def backfill_wheel_prize_reasons(engine: Engine) -> int:
    """Исправляет исторические транзакции выигрышей ленты: reason='achievement' -> 'wheel_prize'."""
    inspector = inspect(engine)
    if "pts_transactions" not in inspector.get_table_names():
        return 0

    with engine.begin() as connection:
        result = connection.execute(
            text(
                "UPDATE pts_transactions "
                "SET reason = 'wheel_prize' "
                "WHERE reason = 'achievement' AND ref_type = 'wheel_prize'"
            )
        )
        updated = result.rowcount if result.rowcount is not None else 0

    if updated > 0:
        logger.info("Бэкфилл выигрышей ленты: обновлено строк: %d", updated)
    return updated

