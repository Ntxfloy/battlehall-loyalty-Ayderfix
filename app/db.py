from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Создаёт таблицы и досинхронизирует колонки.

    Для MVP этого хватает; перед продом сюда встанет Alembic —
    см. app/schema_sync.py о том, чего этот механизм заведомо не умеет."""
    from app import models  # noqa: F401  — регистрация моделей в метаданных
    from app.schema_sync import backfill_wheel_prize_reasons, sync_sqlite_columns

    Base.metadata.create_all(bind=engine)
    sync_sqlite_columns(engine)
    backfill_wheel_prize_reasons(engine)

