from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

_is_sqlite = settings.database_url.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}
engine = create_engine(settings.database_url, connect_args=_connect_args, pool_pre_ping=True)

if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """Создаёт таблицы и досинхронизирует колонки и индексы.

    Для MVP этого хватает; перед продом сюда встанет Alembic —
    см. app/schema_sync.py о том, чего этот механизм заведомо не умеет."""
    from app import models  # noqa: F401  — регистрация моделей в метаданных
    from app.schema_sync import (
        backfill_wheel_prize_reasons,
        sync_sqlite_columns,
        sync_sqlite_indexes,
    )

    Base.metadata.create_all(bind=engine)
    sync_sqlite_columns(engine)
    # Индексы — строго после колонок: уникальный индекс по новой колонке
    # иначе не создать.
    sync_sqlite_indexes(engine)
    backfill_wheel_prize_reasons(engine)
