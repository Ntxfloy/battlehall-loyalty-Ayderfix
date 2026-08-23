"""Права администраторов.

Владелец (owner) имеет всё и не редактируется. Сотруднику (staff) владелец
выдаёт набор прав из списка ниже — например, аккаунту на администраторском ПК
клуба обычно нужны только `codes.view` и `codes.submit`.

Права хранятся JSON-списком в `AdminUser.permissions`, чтобы добавление
нового права не требовало миграции.
"""

import json

from app.models import AdminRole, AdminUser

# --- каталог прав ---

CODES_VIEW = "codes.view"          # видеть код, гостя, его логин и телефон
CODES_SUBMIT = "codes.submit"      # вносить код на стойке (уходит на аппрув)
CODES_APPROVE = "codes.approve"    # подтверждать внесённые коды
USERS_VIEW = "users.view"
PTS_GRANT = "pts.grant"            # начислять/списывать PTS вручную
CATALOG_EDIT = "catalog.edit"      # достижения, награды, ЛУДЛЕНТА
CLUBS_EDIT = "clubs.edit"
REPORTS_VIEW = "reports.view"
LOGS_VIEW = "logs.view"
TEST_TOOLS = "test.tools"          # отправка тестовых вебхуков
ADMINS_MANAGE = "admins.manage"    # управление учётками (только владелец)
OASYS_VIEW = "oasys.view"          # живая карта зала и кассовая статистика из API OASys

ALL_PERMISSIONS: tuple[str, ...] = (
    CODES_VIEW,
    CODES_SUBMIT,
    CODES_APPROVE,
    USERS_VIEW,
    PTS_GRANT,
    CATALOG_EDIT,
    CLUBS_EDIT,
    REPORTS_VIEW,
    LOGS_VIEW,
    TEST_TOOLS,
    ADMINS_MANAGE,
    OASYS_VIEW,
)

# Человеческие названия для интерфейса
LABELS: dict[str, str] = {
    CODES_VIEW: "Видеть коды и данные гостя",
    CODES_SUBMIT: "Вносить коды на стойке",
    CODES_APPROVE: "Подтверждать внесённые коды",
    USERS_VIEW: "Смотреть пользователей",
    PTS_GRANT: "Начислять PTS вручную",
    CATALOG_EDIT: "Править достижения, награды и ЛУДЛЕНТУ",
    CLUBS_EDIT: "Управлять клубами сети",
    REPORTS_VIEW: "Смотреть отчёты",
    LOGS_VIEW: "Смотреть журнал действий",
    TEST_TOOLS: "Отправлять тестовые запросы",
    ADMINS_MANAGE: "Управлять учётками администраторов",
    OASYS_VIEW: "Смотреть живую карту зала и кассу (OASys)",
}

# Права, которые нельзя выдать сотруднику: аппрув и управление учётками —
# смысл разделения ролей в том, что сотрудник не подтверждает сам себя.
OWNER_ONLY: frozenset[str] = frozenset({ADMINS_MANAGE, CODES_APPROVE})

# Что предлагаем по умолчанию для аккаунта на администраторском ПК клуба
DEFAULT_STAFF: tuple[str, ...] = (CODES_VIEW, CODES_SUBMIT)


def parse(raw: str | None) -> set[str]:
    if not raw:
        return set()
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return set()
    if not isinstance(data, list):
        return set()
    return {p for p in data if p in ALL_PERMISSIONS}


def validate_assignable(codes: list[str], *, target_is_owner: bool = False) -> list[str]:
    """Единственная точка проверки набора прав перед записью в БД.

    Неизвестное право — ошибка (неизвестное право не сохраняется молча).
    Права OWNER_ONLY отбрасываются для сотрудников, чтобы сотрудник не смог
    выдать себе права владельца.
    """
    unknown = [c for c in codes if c not in ALL_PERMISSIONS]
    if unknown:
        raise ValueError(f"Неизвестные права: {', '.join(sorted(unknown))}")

    clean = [c for c in codes if c in ALL_PERMISSIONS]
    if not target_is_owner:
        clean = [c for c in clean if c not in OWNER_ONLY]
    return sorted(set(clean))



def dump(permissions, target_is_owner: bool = False) -> str:
    clean = validate_assignable(list(permissions), target_is_owner=target_is_owner)
    return json.dumps(clean)



def granted(admin: AdminUser) -> set[str]:
    if admin.role == AdminRole.OWNER:
        return set(ALL_PERMISSIONS)
    return parse(admin.permissions) - OWNER_ONLY



def has(admin: AdminUser, permission: str) -> bool:
    return permission in granted(admin)
