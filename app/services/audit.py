"""Журнал действий администраторов. Пишем после каждого чувствительного
действия в панели: погашение кода, ручное начисление, отправка тестового
вебхука, создание клуба — чтобы потом можно было ответить на вопрос
«кто начислил эти PTS и когда»."""

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AdminActionLog


def log(
    db: Session,
    admin: str,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: dict | None = None,
) -> None:
    db.add(
        AdminActionLog(
            admin_username=admin,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=json.dumps(detail, ensure_ascii=False) if detail else None,
        )
    )
    db.flush()


def recent(db: Session, limit: int = 100, offset: int = 0, action: str | None = None) -> list[AdminActionLog]:
    stmt = select(AdminActionLog).order_by(AdminActionLog.created_at.desc(), AdminActionLog.id.desc())
    if action:
        stmt = stmt.where(AdminActionLog.action == action)
    return list(db.execute(stmt.offset(offset).limit(limit)).scalars())
