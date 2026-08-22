"""Клубы сети: справочник и агрегированная отчётность по посещаемости."""

import secrets
import string

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Club, GameSession, User

_TOKEN_ALPHABET = string.ascii_letters + string.digits


class ClubError(Exception):
    pass


def generate_token() -> str:
    return "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(40))


def list_clubs(db: Session, only_active: bool = False) -> list[Club]:
    stmt = select(Club).order_by(Club.name)
    if only_active:
        stmt = stmt.where(Club.is_active.is_(True))
    return list(db.execute(stmt).scalars())


def get_by_slug(db: Session, slug: str) -> Club | None:
    return db.execute(select(Club).where(Club.slug == slug)).scalar_one_or_none()


def create(db: Session, slug: str, name: str, token: str | None = None) -> Club:
    if get_by_slug(db, slug) is not None:
        raise ClubError(f"Клуб с slug «{slug}» уже существует")
    club = Club(slug=slug, name=name, oasys_webhook_token=token or generate_token())
    db.add(club)
    db.commit()
    return club


def update(db: Session, club: Club, *, name: str | None = None, is_active: bool | None = None) -> Club:
    if name is not None:
        club.name = name
    if is_active is not None:
        club.is_active = is_active
    db.add(club)
    db.commit()
    return club


def rotate_token(db: Session, club: Club) -> Club:
    club.oasys_webhook_token = generate_token()
    db.add(club)
    db.commit()
    return club


# --- отчётность ---
# ВАЖНО: PTS и награды в программе — общий кошелёк на всю сеть (гость
# копит и тратит их в любом клубе), поэтому per-club тут только то, что
# физически привязано к визиту: сессии, часы, зоны, уникальные гости.

def club_report(db: Session, club: Club, date_from: str | None = None, date_to: str | None = None) -> dict:
    stmt = select(GameSession).where(GameSession.club_id == club.id)
    if date_from:
        stmt = stmt.where(GameSession.game_day >= date_from)
    if date_to:
        stmt = stmt.where(GameSession.game_day <= date_to)
    rows = list(db.execute(stmt).scalars())

    minutes = sum(r.duration_minutes for r in rows)
    by_zone: dict[str, int] = {}
    for r in rows:
        by_zone[r.zone_type] = by_zone.get(r.zone_type, 0) + r.duration_minutes

    unique_guests = len({r.user_id for r in rows})
    unique_days = len({r.game_day for r in rows})

    return {
        "club": {"id": club.id, "slug": club.slug, "name": club.name},
        "sessions": len(rows),
        "unique_guests": unique_guests,
        "unique_game_days": unique_days,
        "total_minutes": minutes,
        "total_hours": round(minutes / 60, 1),
        "minutes_by_zone_type": by_zone,
    }


def network_summary(db: Session, date_from: str | None = None, date_to: str | None = None) -> dict:
    clubs = list_clubs(db)
    reports = [club_report(db, c, date_from, date_to) for c in clubs]
    total_users = db.execute(select(func.count(User.id))).scalar_one()
    return {
        "clubs": reports,
        "total_users_in_program": total_users,
        "total_sessions": sum(r["sessions"] for r in reports),
        "total_hours": round(sum(r["total_hours"] for r in reports), 1),
    }
