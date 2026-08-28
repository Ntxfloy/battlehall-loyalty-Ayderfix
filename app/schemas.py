"""Схемы запросов и ответов API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

MAX_SESSION_MINUTES = 24 * 60
RewardKindValue = Literal["cash", "premium"]
PayoutUnitValue = Literal["RUB", "MONTHS"]
PrizeKindValue = Literal["pts", "reward", "nothing"]
PrizeRarityValue = Literal["common", "rare", "epic", "legendary"]
AchievementUnitValue = Literal["раз", "дн.", "зон", "мин", "PTS"]


class GrantPtsRequest(BaseModel):
    telegram_id: int
    amount: int = Field(..., ge=1, le=1_000_000)
    comment: str = Field(default="Ручное начисление", min_length=1, max_length=200)


class ManualPtsRequest(BaseModel):
    amount: int = Field(..., ge=-1_000_000, le=1_000_000)
    comment: str = Field(default="Ручное начисление", max_length=200)

    @model_validator(mode="after")
    def _not_zero(self):
        if self.amount == 0:
            raise ValueError("Сумма не может быть нулевой")
        return self


class SessionStartPayload(BaseModel):
    """Событие «гость сел за ПК».

    Идентификатор гостя присылаем любым из трёх полей — какое есть в OASys,
    то и используем; резолвим по приоритету client_id -> telegram_id -> phone.
    """

    session_id: str = Field(..., min_length=1, max_length=64, description="ID сессии в OASys, ключ идемпотентности")
    pc_number: int = Field(..., ge=1, le=49)
    started_at: datetime

    client_id: str | None = Field(default=None, min_length=1, max_length=64)
    telegram_id: int | None = None
    phone: str | None = Field(default=None, min_length=3, max_length=32)


class SessionEndPayload(SessionStartPayload):
    """Событие «гость закончил». started_at нужен на случай, если старт потеряли."""

    ended_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=0, le=MAX_SESSION_MINUTES)

    @model_validator(mode="after")
    def _end_not_before_start(self):
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("ended_at не может быть раньше started_at")
        return self


class BookingPayload(BaseModel):
    """Событие «бронь» — ПРЕДЛОЖЕННЫЙ контракт, ждём подтверждения от OASys.
    Резолвим гостя тем же приоритетом, что и сессии: client_id -> telegram_id -> phone."""

    booking_id: str = Field(..., min_length=1, max_length=64, description="ID брони в OASys, ключ идемпотентности")
    status: Literal["created", "confirmed", "cancelled", "no_show", "completed"]
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    pc_number: int | None = Field(default=None, ge=1, le=49)
    price: float = Field(default=0, ge=0)

    client_id: str | None = Field(default=None, min_length=1, max_length=64)
    telegram_id: int | None = None
    phone: str | None = Field(default=None, min_length=3, max_length=32)


class PurchasePayload(BaseModel):
    """Событие «покупка пакета часов/тарифа» — ПРЕДЛОЖЕННЫЙ контракт.
    sku матчится на ачивку в app/services/purchases.py (SKU_ACHIEVEMENTS)."""

    purchase_id: str = Field(..., min_length=1, max_length=64, description="ID покупки в OASys, ключ идемпотентности")
    sku: str = Field(..., min_length=1, max_length=64, description="например pack_3h, pack_5h")
    amount: float = Field(default=0, ge=0)
    purchased_at: datetime

    client_id: str | None = Field(default=None, min_length=1, max_length=64)
    telegram_id: int | None = None
    phone: str | None = Field(default=None, min_length=3, max_length=32)


class BalanceOperationPayload(BaseModel):
    """Событие «движение денег на счёте гостя в OASys» — НЕ PTS. Заменяет
    пуллинг GET /method/admin/operations/history из oasys_live.py."""

    operation_id: str = Field(..., min_length=1, max_length=64, description="ID операции в OASys, ключ идемпотентности")
    operation_type: Literal["increase", "decrease"]
    amount: float = Field(default=0, ge=0)
    payment_method: str | None = Field(default=None, max_length=32)
    created_at: datetime

    client_id: str | None = Field(default=None, min_length=1, max_length=64)
    telegram_id: int | None = None
    phone: str | None = Field(default=None, min_length=3, max_length=32)


class LinkPhonePayload(BaseModel):
    """Привязка телефона к Telegram — приезжает из существующего бота OASys."""

    telegram_id: int
    phone: str = Field(..., min_length=3, max_length=32)
    client_id: str | None = Field(default=None, min_length=1, max_length=64)


class RedeemRequest(BaseModel):
    reward_id: int


class ClaimRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)


class UseCodeRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)


class AdminLoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class ClubCreateRequest(BaseModel):
    slug: str = Field(..., min_length=2, max_length=32, pattern=r"^[a-z0-9_-]+$")
    name: str = Field(..., min_length=1, max_length=128)


class ClubUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    is_active: bool | None = None


class TestSessionStartRequest(BaseModel):
    """Форма «отправить тестовый вебхук» в панели — тот же путь, что у
    настоящего вебхука OASys, только вызывается напрямую из UI администратора."""

    __test__ = False

    club_slug: str = Field(..., min_length=2, max_length=32)
    telegram_id: int
    pc_number: int = Field(..., ge=1, le=49)
    session_id: str | None = Field(default=None, min_length=1, max_length=64)
    started_at: datetime | None = None


class TestSessionEndRequest(BaseModel):
    __test__ = False

    club_slug: str = Field(..., min_length=2, max_length=32)
    telegram_id: int
    session_id: str = Field(..., min_length=1, max_length=64)
    pc_number: int | None = Field(default=None, ge=1, le=49)
    started_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=0, le=MAX_SESSION_MINUTES)


class TestBookingRequest(BaseModel):
    """Форма «отправить тестовый вебхук» в панели — бронь. Тот же путь,
    что у настоящего вебхука OASys, только для ручной проверки/демонстрации."""

    __test__ = False

    club_slug: str = Field(..., min_length=2, max_length=32)
    telegram_id: int
    status: Literal["created", "confirmed", "cancelled", "no_show", "completed"] = "completed"
    pc_number: int | None = Field(default=None, ge=1, le=49)
    price: float = Field(default=0, ge=0)
    booking_id: str | None = Field(default=None, min_length=1, max_length=64)


class TestPurchaseRequest(BaseModel):
    __test__ = False

    club_slug: str = Field(..., min_length=2, max_length=32)
    telegram_id: int
    sku: str = Field(..., min_length=1, max_length=64)
    amount: float = Field(default=0, ge=0)
    purchase_id: str | None = Field(default=None, min_length=1, max_length=64)


class TestBalanceOperationRequest(BaseModel):
    __test__ = False

    club_slug: str = Field(..., min_length=2, max_length=32)
    telegram_id: int
    operation_type: Literal["increase", "decrease"] = "increase"
    amount: float = Field(default=0, ge=0)
    payment_method: str = "card"
    operation_id: str | None = Field(default=None, min_length=1, max_length=64)


class AdminCreateRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=128)
    permissions: list[str] = Field(default_factory=list, max_length=100)
    club_id: int | None = None


class AdminUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    permissions: list[str] | None = Field(default=None, max_length=100)
    is_active: bool | None = None
    club_id: int | None = None


class AdminPasswordRequest(BaseModel):
    password: str = Field(..., min_length=8, max_length=128)


class SelfPasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


class CodeActionRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)


class AchievementUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    unit: AchievementUnitValue | None = None
    target: int | None = Field(default=None, ge=1)
    reward_pts: int | None = Field(default=None, ge=0)
    sort_order: int | None = None
    is_active: bool | None = None


class RewardCreateRequest(BaseModel):
    code: str = Field(..., min_length=2, max_length=64)
    title: str = Field(..., min_length=1, max_length=128)
    kind: RewardKindValue = "cash"
    description: str = Field(default="", max_length=2000)
    cost_pts: int = Field(..., ge=0)
    payout_value: float = Field(default=0, ge=0)
    payout_unit: PayoutUnitValue = "RUB"
    sort_order: int = 0
    is_active: bool = True


class RewardUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=128)
    kind: RewardKindValue | None = None
    description: str | None = Field(default=None, max_length=2000)
    cost_pts: int | None = Field(default=None, ge=0)
    payout_value: float | None = Field(default=None, ge=0)
    payout_unit: PayoutUnitValue | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class WheelCreateRequest(BaseModel):
    code: str = Field(..., min_length=2, max_length=64)
    title: str = Field(..., min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    cost_pts: int = Field(..., gt=0)
    sort_order: int = 0
    is_active: bool = True


class WheelUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    cost_pts: int | None = Field(default=None, gt=0)
    sort_order: int | None = None
    is_active: bool | None = None


class PrizeRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=128)
    kind: PrizeKindValue = "pts"
    rarity: PrizeRarityValue = "common"
    pts_amount: int = Field(default=0, ge=0)
    reward_id: int | None = None
    weight: int = Field(default=1, ge=0)
    sort_order: int = 0
    is_active: bool = True


class PrizeUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=128)
    kind: PrizeKindValue | None = None
    rarity: PrizeRarityValue | None = None
    pts_amount: int | None = Field(default=None, ge=0)
    reward_id: int | None = None
    weight: int | None = Field(default=None, ge=0)
    sort_order: int | None = None
    is_active: bool | None = None


class SpinRequest(BaseModel):
    wheel_id: int
    count: Literal[1, 5, 10] = 1
    all_in: bool = False
