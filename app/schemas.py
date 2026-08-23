"""Схемы запросов и ответов API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

MAX_SESSION_MINUTES = 24 * 60


class GrantPtsRequest(BaseModel):
    telegram_id: int
    amount: int = Field(..., ge=1, le=1_000_000)
    comment: str = Field(default="Ручное начисление", min_length=1, max_length=200)


class ManualPtsRequest(BaseModel):
    amount: int = Field(..., ge=-1_000_000, le=1_000_000)
    comment: str = Field(default="Ручное начисление", max_length=200)

    @field_validator("amount")
    @classmethod
    def _not_zero(cls, v: int) -> int:
        if v == 0:
            raise ValueError("Сумма не может быть нулевой")
        return v


class SessionStartPayload(BaseModel):
    """Событие «гость сел за ПК».

    Идентификатор гостя присылаем любым из трёх полей — какое есть в OASys,
    то и используем; резолвим по приоритету client_id -> telegram_id -> phone.
    """

    session_id: str = Field(..., description="ID сессии в OASys, ключ идемпотентности")
    pc_number: int = Field(..., ge=1, le=49)
    started_at: datetime

    client_id: str | None = None
    telegram_id: int | None = None
    phone: str | None = None

    ended_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=0, le=MAX_SESSION_MINUTES)


class SessionEndPayload(SessionStartPayload):
    """Событие «гость закончил». started_at нужен на случай, если старт потеряли."""

    ended_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=0, le=MAX_SESSION_MINUTES)


class LinkPhonePayload(BaseModel):
    """Привязка телефона к Telegram — приезжает из существующего бота OASys."""

    telegram_id: int
    phone: str
    client_id: str | None = None


class RedeemRequest(BaseModel):
    reward_id: int


class ClaimRequest(BaseModel):
    code: str


class UseCodeRequest(BaseModel):
    code: str


class AdminLoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class ClubCreateRequest(BaseModel):
    slug: str = Field(..., min_length=2, max_length=32, pattern=r"^[a-z0-9_-]+$")
    name: str = Field(..., min_length=1, max_length=128)


class ClubUpdateRequest(BaseModel):
    name: str | None = None
    is_active: bool | None = None


class TestSessionStartRequest(BaseModel):
    """Форма «отправить тестовый вебхук» в панели — тот же путь, что у
    настоящего вебхука OASys, только вызывается напрямую из UI администратора."""

    __test__ = False

    club_slug: str
    telegram_id: int
    pc_number: int = Field(..., ge=1, le=49)
    session_id: str | None = None
    started_at: datetime | None = None


class TestSessionEndRequest(BaseModel):
    __test__ = False

    club_slug: str
    telegram_id: int
    session_id: str
    pc_number: int | None = None
    started_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=0, le=MAX_SESSION_MINUTES)


class AdminCreateRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=8, max_length=128)
    display_name: str = ""
    permissions: list[str] = Field(default_factory=list)
    club_id: int | None = None


class AdminUpdateRequest(BaseModel):
    display_name: str | None = None
    permissions: list[str] | None = None
    is_active: bool | None = None
    club_id: int | None = None


class AdminPasswordRequest(BaseModel):
    password: str = Field(..., min_length=8, max_length=128)


class SelfPasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


class CodeActionRequest(BaseModel):
    code: str


class AchievementUpdateRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    unit: str | None = None
    target: int | None = Field(default=None, ge=1)
    reward_pts: int | None = Field(default=None, ge=0)
    sort_order: int | None = None
    is_active: bool | None = None


class RewardCreateRequest(BaseModel):
    code: str = Field(..., min_length=2, max_length=64)
    title: str = Field(..., min_length=1, max_length=128)
    kind: str = "cash"
    description: str = ""
    cost_pts: int = Field(..., ge=0)
    payout_value: float = 0
    payout_unit: str = "RUB"
    sort_order: int = 0
    is_active: bool = True


class RewardUpdateRequest(BaseModel):
    title: str | None = None
    kind: str | None = None
    description: str | None = None
    cost_pts: int | None = Field(default=None, ge=0)
    payout_value: float | None = None
    payout_unit: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class WheelCreateRequest(BaseModel):
    code: str = Field(..., min_length=2, max_length=64)
    title: str = Field(..., min_length=1, max_length=128)
    description: str = ""
    cost_pts: int = Field(..., gt=0)
    sort_order: int = 0
    is_active: bool = True


class WheelUpdateRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    cost_pts: int | None = Field(default=None, gt=0)
    sort_order: int | None = None
    is_active: bool | None = None


class PrizeRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=128)
    kind: str = "pts"
    rarity: str = "common"
    pts_amount: int = Field(default=0, ge=0)
    reward_id: int | None = None
    weight: int = Field(default=1, ge=0)
    sort_order: int = 0
    is_active: bool = True


class PrizeUpdateRequest(BaseModel):
    title: str | None = None
    kind: str | None = None
    rarity: str | None = None
    pts_amount: int | None = Field(default=None, ge=0)
    reward_id: int | None = None
    weight: int | None = Field(default=None, ge=0)
    sort_order: int | None = None
    is_active: bool | None = None


class SpinRequest(BaseModel):
    wheel_id: int
    count: Literal[1, 5, 10] = 1
    all_in: bool = False
