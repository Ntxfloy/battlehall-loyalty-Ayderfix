"""Живые данные из внутреннего API OASys (не вебхуки).

Вебхуки (app/api/webhooks.py) OASys присылает сам, по событию сессии.
Здесь — обратное: сервер сам дёргает тот же API, которым пользуется
официальный Windows-клиент админа зала. Эндпоинты вскрыты перехватом
собственного авторизованного трафика этого клиента через Fiddler
(см. oASys_bot/oASys/*.har) — публичной документации у API нет.

Нужно для внутренней демки: живая карта зала поверх уже работающей карты
зон (app/zones.py), без ожидания вебхуков.

Интеграция необязательная, как и Google Sheets (app/services/sheets.py):
без OASYS_JWT/OASYS_PC_JWT эти ручки просто отдают понятную ошибку.
"""

import logging

import httpx

from app.config import get_settings
from app.zones import parse_overrides, zone_for_pc_in_club

logger = logging.getLogger(__name__)
settings = get_settings()

_TIMEOUT = 8.0


class OasysLiveError(Exception):
    pass


def is_configured() -> bool:
    return bool(settings.oasys_jwt and settings.oasys_pc_jwt and settings.oasys_base_url)


def _headers() -> dict:
    return {
        "Host": settings.oasys_api_host,
        "Accept": "application/json",
        "User-Agent": settings.oasys_user_agent,
        "jwt": settings.oasys_jwt,
        "pc-jwt": settings.oasys_pc_jwt,
    }


def _require_configured() -> None:
    if not is_configured():
        raise OasysLiveError("OASys API не настроен: нужны OASYS_JWT и OASYS_PC_JWT в .env")


def _unwrap(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError as exc:
        raise OasysLiveError(f"OASys вернул не-JSON ответ (HTTP {response.status_code})") from exc
    if not data.get("ok"):
        raise OasysLiveError(f"OASys вернул ошибку: {data}")
    return data["result"]


def _get(path: str, params: dict | None = None) -> dict:
    _require_configured()
    try:
        r = httpx.get(
            f"{settings.oasys_base_url}{path}",
            headers=_headers(),
            params=params,
            verify=False,
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise OasysLiveError(f"OASys недоступен: {exc}") from exc
    return _unwrap(r)


def _post(path: str, payload: dict) -> dict:
    _require_configured()
    try:
        r = httpx.post(
            f"{settings.oasys_base_url}{path}",
            headers={**_headers(), "Content-Type": "application/json; charset=utf-8"},
            json=payload,
            verify=False,
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise OasysLiveError(f"OASys недоступен: {exc}") from exc
    return _unwrap(r)


def live_map(club_pc_zone_overrides: str | None = None) -> dict:
    """Карта зала: занятость по каждому ПК + название зоны программы лояльности
    (учитывает переопределения конкретного клуба, если переданы — см. Club.pc_zone_overrides)."""
    result = _get("/method/map/")
    overrides = parse_overrides(club_pc_zone_overrides)
    devices = []
    for device in result.get("devices", []):
        zone = zone_for_pc_in_club(device.get("number", 0), overrides)
        devices.append({**device, "loyalty_zone_title": zone.title if zone else None})
    return {"count": result.get("count", 0), "in_use": result.get("in_use", 0), "devices": devices}


def pc_user_info(pc_number: int) -> dict:
    """Кто сейчас сидит за конкретным ПК и на каком тарифе — по номеру, не по id."""
    return _get("/method/map/user_info/", params={"pc_number": pc_number})


def cashier_stats() -> dict:
    """Статистика текущей смены кассы (сумма операций, баланс кассы нал/безнал)."""
    return _get("/method/admin/cashier/stats")


def club_discounts() -> list:
    return _get("/method/discount/club_discount/list")


def promo_codes() -> list:
    return _get("/method/discount/promo/listV2")


def search_user(query: str) -> list:
    """Поиск по телефону/логину/имени — то же, чем пользуется стойка в клиенте OASys."""
    return _get("/method/user/search", params={"q": query})


def close_session(username: str) -> str:
    return _get("/method/session/close", params={"username": username})
