import os
import tempfile
from pathlib import Path

import pytest

# Настройки читаются на импорте, поэтому окружение готовим до импорта приложения.
_TMP = Path(tempfile.mkdtemp(prefix="battlehall-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["BOT_TOKEN"] = "test:token"
os.environ["DEV_ALLOW_FAKE_AUTH"] = "false"
os.environ["REFUND_PTS_ON_EXPIRE"] = "true"
os.environ["MINIAPP_URL"] = "https://example.test/app"
os.environ["ADMIN_SESSION_SECRET"] = "test-session-secret"
os.environ["ADMIN_DEFAULT_USERNAME"] = "admin"
os.environ["ADMIN_DEFAULT_PASSWORD"] = "test-password-123"

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Club, User  # noqa: E402
from app.services import referrals  # noqa: E402
from seed import seed_achievements, seed_default_admin, seed_default_club, seed_rewards  # noqa: E402


@pytest.fixture
def db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    seed_achievements(session, force=True)
    seed_rewards(session, force=True)
    seed_default_club(session)
    seed_default_admin(session)
    session.commit()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def club(db):
    return db.query(Club).filter(Club.slug == "main").one()


@pytest.fixture
def user(db):
    row = User(telegram_id=555001, first_name="Тест", referral_code=referrals.generate_code(db))
    db.add(row)
    db.commit()
    return row
