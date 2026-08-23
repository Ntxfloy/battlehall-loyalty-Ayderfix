"""Идемпотентность приёма сессий OASys при гонке INSERT."""

from datetime import datetime, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Club, GameSession, User
from app.schemas import SessionStartPayload
from app.services import sessions


def _payload(session_id: str, user) -> SessionStartPayload:
    return SessionStartPayload(
        session_id=session_id,
        pc_number=15,
        started_at=datetime.now(timezone.utc),
        telegram_id=user.telegram_id,
    )


def test_start_session_duplicate_is_idempotent(db, user, club):
    payload = _payload("same-session", user)
    first, created = sessions.start_session(db, club, payload)
    db.commit()
    second, created_again = sessions.start_session(db, club, payload)
    db.commit()

    assert created is True
    assert created_again is False
    assert first.id == second.id
    count = db.execute(
        select(GameSession).where(
            GameSession.club_id == club.id,
            GameSession.oasys_session_id == "same-session",
        )
    ).scalars().all()
    assert len(count) == 1


def test_start_session_integrity_error_returns_existing(db, user, club, monkeypatch):
    """Если параллельный воркер успел вставить ту же сессию, IntegrityError
    не роняет вебхук и не создаёт вторую строку."""
    payload = _payload("race-session", user)
    original_find = sessions._find
    hijacked = {"done": False}

    def find_then_race(db_sess, club_id, session_id):
        if not hijacked["done"]:
            hijacked["done"] = True
            other = SessionLocal()
            try:
                other_user = other.get(User, user.id)
                other_club = other.get(Club, club.id)
                sessions._new_session(other, other_user, other_club, payload)
                other.commit()
            finally:
                other.close()
            return None
        return original_find(db_sess, club_id, session_id)

    monkeypatch.setattr(sessions, "_find", find_then_race)
    row, created = sessions.start_session(db, club, payload)
    monkeypatch.undo()

    assert created is False
    assert row.oasys_session_id == "race-session"
    with SessionLocal() as verify:
        saved = list(
            verify.execute(
                select(GameSession).where(GameSession.oasys_session_id == "race-session")
            ).scalars()
        )
        assert len(saved) == 1
