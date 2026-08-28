"""Досинхронизация схемы SQLite при старте.

`create_all` умеет только создавать отсутствующие таблицы — добавленную
в модель колонку он в существующей таблице не заведёт, и приложение падает
на «no such column». Пока в проекте нет Alembic, этот модуль закрывает разрыв
для SQLite: сравнивает колонки и индексы модели с фактическими и добавляет
недостающие.

Ограничения намеренные: только ADD COLUMN и CREATE INDEX. Переименование,
смена типа и удаление колонок здесь не делаются — такие изменения требуют
настоящей миграции с переносом данных.

ДЛЯ ПРОДА ЭТОГО НЕДОСТАТОЧНО. Перед запуском на Postgres нужен Alembic.
"""

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

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

    if applied:
        logger.info("Добавлены колонки: %s", ", ".join(applied))
    return applied


def sync_sqlite_indexes(engine: Engine) -> list[str]:
    """Создаёт недостающие индексы для уже существующих таблиц.

    ADD COLUMN индекс не создаёт. Без этого шага уникальный индекс по
    `pts_transactions.idem_key` появился бы только на чистой базе, а на боевой
    защита от двойного списания молча не работала бы.

    Если в таблице уже есть дубли, создание уникального индекса упадёт. Это
    не повод ронять весь старт: логируем и идём дальше, разбор дублей —
    ручная операция.
    """
    if engine.dialect.name != "sqlite":
        return []

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    created: list[str] = []

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue

        table_columns = {col["name"] for col in inspector.get_columns(table.name)}
        present = {idx["name"] for idx in inspector.get_indexes(table.name)}

        for index in table.indexes:
            if index.name in present:
                continue
            if not {col.name for col in index.columns} <= table_columns:
                continue   # колонки ещё нет — её добавит sync_sqlite_columns
            try:
                index.create(bind=engine)
            except SQLAlchemyError as exc:
                logger.warning("Не удалось создать индекс %s: %s", index.name, exc)
            else:
                created.append(index.name)

    if created:
        logger.info("Созданы индексы: %s", ", ".join(created))
    return created


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
