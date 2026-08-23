from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Статусы держим строками, а не Enum-типами: правка набора значений не должна
# требовать миграции типа в Postgres.

class TxReason:
    ACHIEVEMENT = "achievement"          # начисление за забранную ачивку
    REWARD_REDEEM = "reward_redeem"      # списание при обмене на награду
    REWARD_REFUND = "reward_refund"      # возврат за сгоревший код
    TOPUP = "topup"                      # пополнение (TON, пост-MVP)
    MANUAL = "manual"                    # ручная правка админом
    WHEEL_PRIZE = "wheel_prize"          # выигрыш в «ЛУДЛЕНТЕ» — не заработок



class RedemptionStatus:
    """Путь кода награды:
    pending -> submitted (сотрудник внёс код на стойке) -> approved (владелец
    подтвердил, строка готова к выгрузке в таблицу компенсаций).
    Разделение submitted/approved нужно, чтобы сотрудник не мог сам себе
    подтвердить выдачу — аппрув остаётся за владельцем."""

    PENDING = "pending"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class AdminRole:
    OWNER = "owner"   # владелец: полный доступ, управляет учётками и аппрувит
    STAFF = "staff"   # стойка клуба: набор прав выдаёт владелец


class PrizeKind:
    PTS = "pts"           # начисляем PTS на баланс
    REWARD = "reward"     # выдаём код на награду из каталога
    NOTHING = "nothing"   # пусто — утешительная ячейка


class PrizeRarity:
    COMMON = "common"
    RARE = "rare"
    EPIC = "epic"
    LEGENDARY = "legendary"


class AchievementCategory:
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    SPECIAL = "special"


class RewardKind:
    CASH = "cash"                 # PTS -> рубли на игровой счёт
    TELEGRAM_PREMIUM = "premium"  # PTS -> Telegram Premium


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    # Привязка к существующему боту OASys — по ней матчим вебхуки сессий
    phone: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    oasys_client_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)

    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))

    pts_balance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    referral_code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    referred_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # реферал засчитан пригласившему (друг отыграл нужный минимум)
    referral_credited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    referred_by: Mapped["User | None"] = relationship(remote_side=[id], backref="referrals")


class Club(Base):
    """Один клуб сети. У каждого свой токен вебхука OASys и, при необходимости,
    своя карта ПК-зона (в остальных случаях действует общая из app/zones.py —
    она захардкожена по клубу из спеки и не подходит другим залам как есть)."""

    __tablename__ = "clubs"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    oasys_webhook_token: Mapped[str] = mapped_column(String(128))
    # JSON-строка {"1": "DUO_A", ...}: переопределение карты зон для этого клуба.
    # Пусто — используется общая карта из app/zones.py.
    pc_zone_overrides: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AdminUser(Base):
    """Учётка администратора для входа в панель. Пароль хранится только
    в виде PBKDF2-хеша с индивидуальной солью (app/admin_auth.py)."""

    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    display_name: Mapped[str] = mapped_column(String(128), default="")
    role: Mapped[str] = mapped_column(String(16), default=AdminRole.STAFF, nullable=False)
    # JSON-список выданных прав для роли staff (у owner всегда все).
    # Управляется только владельцем, см. app/permissions.py
    permissions: Mapped[str] = mapped_column(Text, default="")
    # Сотрудник может быть привязан к своему клубу сети (null — вся сеть)
    club_id: Mapped[int | None] = mapped_column(ForeignKey("clubs.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminActionLog(Base):
    """Журнал действий администраторов: кто, что и над каким объектом сделал.
    Пишется из app/services/audit.py — не пропускать вызов при добавлении
    нового чувствительного действия в панели."""

    __tablename__ = "admin_action_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    admin_username: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class GameSession(Base):
    """Игровая сессия, приехавшая вебхуком из OASys."""

    __tablename__ = "game_sessions"
    __table_args__ = (
        Index("ix_sessions_user_started", "user_id", "started_at"),
        Index("ix_sessions_club_day", "club_id", "game_day"),
        # session_id уникален только в рамках одного клуба: у разных клубов
        # сети — разные инсталляции OASys, они могут повторно использовать номера.
        UniqueConstraint("club_id", "oasys_session_id", name="uq_session_per_club"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    club_id: Mapped[int] = mapped_column(ForeignKey("clubs.id"), index=True)
    oasys_session_id: Mapped[str] = mapped_column(String(64), index=True)

    pc_number: Mapped[int] = mapped_column(Integer)
    zone_code: Mapped[str] = mapped_column(String(32))
    zone_type: Mapped[str] = mapped_column(String(16))

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # игровой день (с 06:00 МСК), к которому относится сессия
    game_day: Mapped[str] = mapped_column(String(10), index=True)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PtsTransaction(Base):
    """Журнал движения PTS. Источник правды по балансу — сумма журнала."""

    __tablename__ = "pts_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)          # + начисление, - списание
    balance_after: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(32))
    ref_type: Mapped[str | None] = mapped_column(String(32))
    ref_id: Mapped[str | None] = mapped_column(String(64))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AchievementDef(Base):
    """Каталог достижений. Заполняется сидом из app/achievements_defs.py,
    дальше правится из админки без релиза."""

    __tablename__ = "achievement_defs"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(16), index=True)   # weekly | monthly | special
    period: Mapped[str] = mapped_column(String(8))                  # day | week | month | year | all
    target: Mapped[int] = mapped_column(Integer, default=1)
    reward_pts: Mapped[int] = mapped_column(Integer, default=0)
    unit: Mapped[str] = mapped_column(String(16), default="раз")    # подпись к прогрессу
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # false — ачивка описана, но её счётчик ещё не подключён (нужна интеграция)
    is_implemented: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AchievementProgress(Base):
    """Прогресс пользователя по одной ачивке в рамках одного периода."""

    __tablename__ = "achievement_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "achievement_code", "period_key", name="uq_progress"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    achievement_code: Mapped[str] = mapped_column(String(64), index=True)
    period_key: Mapped[str] = mapped_column(String(16), index=True)

    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    target: Mapped[int] = mapped_column(Integer, default=1)
    reward_pts: Mapped[int] = mapped_column(Integer, default=0)
    # уникальные отметки (дни недели, типы зон и т.п.), чтобы не считать одно дважды
    marks: Mapped[str] = mapped_column(Text, default="")

    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # награду гость забирает вручную — до этого PTS не начислены
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Reward(Base):
    __tablename__ = "rewards"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    cost_pts: Mapped[int] = mapped_column(Integer)
    # что получает гость: рубли на счёт или месяцы премиума
    payout_value: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    payout_unit: Mapped[str] = mapped_column(String(16), default="RUB")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class RewardRedemption(Base):
    """Обмен PTS на награду: код живёт 24 часа и гасится админом на стойке."""

    __tablename__ = "reward_redemptions"
    __table_args__ = (
        Index("ix_redemptions_status_expires", "status", "expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    reward_id: Mapped[int] = mapped_column(ForeignKey("rewards.id"))

    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default=RedemptionStatus.PENDING, index=True)
    pts_spent: Mapped[int] = mapped_column(Integer)

    # снимок награды на момент обмена — каталог потом может измениться
    reward_title: Mapped[str] = mapped_column(String(128))
    payout_value: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    payout_unit: Mapped[str] = mapped_column(String(16), default="RUB")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    # Сотрудник внёс код на стойке
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_by: Mapped[str | None] = mapped_column(String(64))
    # Владелец подтвердил выдачу — только после этого строка идёт в таблицу
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(64))

    # отметка, что строка уехала в гугл-таблицу компенсаций
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Момент фактического погашения. Отличается от expires_at: тот показывает,
    # когда код должен был сгореть, этот — когда регламентный прогон его закрыл.
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Момент возврата PTS за сгоревший код. Отдельно от статуса EXPIRED:
    # код может истечь при refund_pts_on_expire=False, и тогда возврата нет.
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Если код выпал из «ЛУДЛЕНТЫ», а не куплен напрямую
    source: Mapped[str] = mapped_column(String(16), default="catalog")




class Wheel(Base):
    """«ЛУДЛЕНТА» — прокрутка за PTS со случайным призом.

    Кроме стоимости прокрутки, состав призов и их веса целиком настраиваются
    в админке: движок ничего не знает про конкретные призы."""

    __tablename__ = "wheels"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    cost_pts: Mapped[int] = mapped_column(Integer)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WheelPrize(Base):
    """Ячейка ленты. Шанс выпадения = weight / сумма весов активных ячеек,
    поэтому веса можно задавать любыми целыми числами."""

    __tablename__ = "wheel_prizes"

    id: Mapped[int] = mapped_column(primary_key=True)
    wheel_id: Mapped[int] = mapped_column(ForeignKey("wheels.id"), index=True)

    title: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16), default=PrizeKind.PTS)
    rarity: Mapped[str] = mapped_column(String(16), default=PrizeRarity.COMMON)

    pts_amount: Mapped[int] = mapped_column(Integer, default=0)
    reward_id: Mapped[int | None] = mapped_column(ForeignKey("rewards.id"))

    weight: Mapped[int] = mapped_column(Integer, default=1)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class WheelSpin(Base):
    """История прокруток — нужна и гостю («что мне выпало»), и владельцу,
    чтобы видеть реальную отдачу ленты против настроенных весов."""

    __tablename__ = "wheel_spins"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    wheel_id: Mapped[int] = mapped_column(ForeignKey("wheels.id"), index=True)
    prize_id: Mapped[int | None] = mapped_column(ForeignKey("wheel_prizes.id"))

    cost_pts: Mapped[int] = mapped_column(Integer)
    prize_title: Mapped[str] = mapped_column(String(128))
    prize_kind: Mapped[str] = mapped_column(String(16))
    prize_rarity: Mapped[str] = mapped_column(String(16), default=PrizeRarity.COMMON)
    pts_won: Mapped[int] = mapped_column(Integer, default=0)
    redemption_id: Mapped[int | None] = mapped_column(ForeignKey("reward_redemptions.id"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
