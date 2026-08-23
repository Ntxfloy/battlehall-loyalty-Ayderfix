import hmac
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Дефолт — прод: забытая переменная окружения не должна ослаблять защиту.
    app_env: str = "production"

    database_url: str = "sqlite:///./battlehall.db"

    bot_token: str = ""
    bot_username: str = ""      # без @, для реферальных ссылок
    miniapp_url: str = ""
    channel_id: str = ""

    # Токен вебхука клуба по умолчанию — используется, только пока в базе
    # нет ни одной записи Club (seed.py создаёт из него клуб "main").
    # Дальше каждый клуб сети хранит свой токен в таблице clubs.
    oasys_webhook_token: str = "change-me"

    # Сервисный токен для программного доступа к /api/admin/* (скрипты,
    # выгрузка в таблицу компенсаций). Админка в браузере входит по паролю.
    admin_token: str = "change-me"
    admin_session_secret: str = "change-me-session-secret"
    admin_session_ttl_hours: int = 12
    admin_default_username: str = "admin"
    admin_default_password: str = "change-me-now"

    game_day_start_hour: int = 6
    reward_code_ttl_hours: int = 24
    refund_pts_on_expire: bool = True
    daily_checkin_pts: int = 50
    referral_min_minutes: int = 60

    # --- Google Sheets (необязательно) ---
    # Пусто — интеграция выключена, выгрузка отдаёт JSON для ручного переноса.
    google_sheet_id: str = ""
    google_credentials_file: str = ""
    google_sheet_worksheet: str = "Компенсации"
    # Отправлять строку в таблицу сразу при подтверждении кода
    google_autoexport: bool = True

    dev_allow_fake_auth: bool = False
    dev_fake_telegram_id: int = 100001

    # Пусто — выключено. Если задано, весь сайт (мини-апп + админка) закрыт
    # общим паролем поверх обычной авторизации — щит на время демо по
    # публичной ссылке (туннель), чтобы код доступа не гулял по интернету
    # неограниченно широко.
    demo_gate_password: str = ""

    # --- OASys, живые данные из внутреннего API (необязательно) ---
    # Пусто — интеграция выключена. Это не вебхуки (те приходят сами, см.
    # OASYS_WEBHOOK_TOKEN выше), а вызовы того же API, которым пользуется
    # официальный Windows-клиент админа зала — для живой карты, кассовой
    # статистики и списков скидок/промокодов прямо из панели.
    oasys_base_url: str = "https://194.87.187.41"
    oasys_api_host: str = "api.oasystem.ru"
    oasys_user_agent: str = "OasysAdmin/1.140.0.0"
    oasys_jwt: str = ""
    oasys_pc_jwt: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


_LOCAL_ENVS = frozenset({"dev", "development", "local", "test", "testing", "ci"})
_PLACEHOLDER_SECRETS = frozenset({
    "", "change-me", "change-me-too", "changeme", "change-me-now", "changeme-now",
    "change-me-session-secret", "secret", "token", "admin", "password", "battlehall",
})
MIN_SECRET_LEN = 32


def is_local_env() -> bool:
    return (get_settings().app_env or "").strip().lower() in _LOCAL_ENVS


def is_production() -> bool:
    return not is_local_env()


def is_placeholder_secret(value: str | None) -> bool:
    """Дефолтный или слишком короткий секрет считаем отсутствующим."""
    candidate = (value or "").strip()
    return candidate.lower() in _PLACEHOLDER_SECRETS or len(candidate) < MIN_SECRET_LEN


def secrets_match(provided: str | None, expected: str | None) -> bool:
    """Безопасная постоянная по времени проверка равенства секретов."""
    if is_placeholder_secret(expected) or is_placeholder_secret(provided):
        return False
    expected_val = (expected or "").strip()
    try:
        provided_bytes = (provided or "").encode("utf-8")
        expected_bytes = expected_val.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(provided_bytes, expected_bytes)
