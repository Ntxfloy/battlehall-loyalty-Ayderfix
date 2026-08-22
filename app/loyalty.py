"""Группы лояльности: считаются по часам, отыгранным за календарный год.

ВНИМАНИЕ: пороги и проценты — заглушка по образцу скриншота («1. Новичок», 1%,
прогресс 2.6/50 ч). Реальную лесенку должен подтвердить клуб, после чего её
достаточно поправить здесь.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class LoyaltyGroup:
    level: int
    title: str
    min_hours_year: float
    discount_percent: int


GROUPS: tuple[LoyaltyGroup, ...] = (
    LoyaltyGroup(1, "Новичок", 0, 1),
    LoyaltyGroup(2, "Игрок", 50, 3),
    LoyaltyGroup(3, "Боец", 150, 5),
    LoyaltyGroup(4, "Ветеран", 300, 7),
    LoyaltyGroup(5, "Легенда", 600, 10),
)


def group_for_hours(hours_year: float) -> LoyaltyGroup:
    current = GROUPS[0]
    for group in GROUPS:
        if hours_year >= group.min_hours_year:
            current = group
    return current


def next_group(group: LoyaltyGroup) -> LoyaltyGroup | None:
    return GROUPS[group.level] if group.level < len(GROUPS) else None
