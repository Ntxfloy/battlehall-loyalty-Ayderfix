"""Карта зон клуба по номерам ПК.

Захардкожено намеренно: все 49 машин распределены без пробелов, состав зала
меняется раз в несколько лет — держать это отдельной таблицей в БД избыточно.
"""

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Zone:
    code: str          # машинное имя конкретной зоны
    title: str         # как показываем гостю
    zone_type: str     # укрупнённый тип, их ровно 5 — по ним считается ачивка «все зоны»


ZONES: tuple[Zone, ...] = (
    Zone("STANDARD_HALL", "СТАНДАРТ ЗАЛ", "STANDARD"),
    Zone("VIP_A", "ВИП А", "VIP"),
    Zone("VIP_B", "ВИП Б", "VIP"),
    Zone("DUO_A", "ДУО А", "DUO"),
    Zone("DUO_B", "ДУО Б", "DUO"),
    Zone("TRIO", "ТРИО", "TRIO"),
    Zone("SOLO", "СОЛО", "SOLO"),
)

_ZONE_BY_CODE = {z.code: z for z in ZONES}

# Диапазоны/списки номеров ПК -> код зоны
_PC_RANGES: tuple[tuple[range | tuple[int, ...], str], ...] = (
    (range(15, 45), "STANDARD_HALL"),   # 15-44
    (range(10, 15), "VIP_A"),           # 10-14
    (range(45, 50), "VIP_B"),           # 45-49
    ((1, 2), "DUO_A"),
    ((4, 5), "DUO_B"),
    ((6, 7, 8), "TRIO"),
    ((3, 9), "SOLO"),
)

PC_TO_ZONE: dict[int, str] = {
    pc: zone_code for numbers, zone_code in _PC_RANGES for pc in numbers
}

TOTAL_PCS = 49
ZONE_TYPES: tuple[str, ...] = ("STANDARD", "VIP", "DUO", "TRIO", "SOLO")


def zone_for_pc(pc_number: int) -> Zone | None:
    code = PC_TO_ZONE.get(pc_number)
    return _ZONE_BY_CODE[code] if code else None


def parse_overrides(raw: str | None) -> dict[int, str]:
    """Разбирает Club.pc_zone_overrides — JSON вида {"1": "DUO_A"}.
    Пустая строка/битый JSON -> нет переопределений, тихо используем общую карту."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {int(pc): str(code) for pc, code in data.items()}
    except (ValueError, TypeError, json.JSONDecodeError):
        return {}


def zone_for_pc_in_club(pc_number: int, overrides: dict[int, str] | None = None) -> Zone | None:
    """Зона по номеру ПК с учётом переопределений конкретного клуба.
    Нужна, потому что у разных клубов сети разная раскладка залов —
    общая PC_TO_ZONE верна только для клуба из исходной спеки."""
    if overrides:
        code = overrides.get(pc_number)
        if code:
            zone = _ZONE_BY_CODE.get(code)
            if zone:
                return zone
    return zone_for_pc(pc_number)


def zone_by_code(code: str) -> Zone | None:
    return _ZONE_BY_CODE.get(code)


def zone_title(code: str) -> str:
    zone = _ZONE_BY_CODE.get(code)
    return zone.title if zone else code
