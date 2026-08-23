import hashlib
import hmac
import html
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import admin, console, desk, manage, miniapp, webhooks
from app.config import get_settings, is_local_env, is_placeholder_secret, is_production
from app.db import SessionLocal, init_db
from app.services import clubs as clubs_service

logger = logging.getLogger(__name__)

settings = get_settings()
BASE_DIR = Path(__file__).resolve().parent.parent
MINIAPP_DIR = BASE_DIR / "miniapp"
ADMIN_UI_DIR = BASE_DIR / "admin"
STATIC_DIR = BASE_DIR / "static"


def safe_redirect(url: str | None) -> str:
    """Только относительный путь того же сайта. Иначе после демо-гейта
    параметр redirect уведёт на чужой домен."""
    candidate = (url or "/").strip()
    if not candidate.startswith("/") or candidate.startswith("//") or "\\" in candidate:
        return "/"
    return candidate


@asynccontextmanager
async def lifespan(_: FastAPI):

    init_db()
    with SessionLocal() as db:
        weak_clubs = [
            c for c in clubs_service.list_clubs(db)
            if c.is_active and is_placeholder_secret(c.oasys_webhook_token)
        ]
        for c in weak_clubs:
            logger.error(
                "Вебхуки активного клуба %s отключены: токен отсутствует или короче 32 символов. Выполните ротацию в панели.",
                c.slug,
            )
    yield


app = FastAPI(
    title="BATTLEHALL Loyalty",
    description="Программа лояльности клуба: Telegram Mini App поверх OASys",
    version="0.1.0",
    lifespan=lifespan,
)

# Mini App грузится с этого же домена, поэтому CORS нужен только для локальной
# отладки фронта на отдельном порту. APP_ENV=local/test тоже локальная среда.
_cors_origins = ["*"] if is_local_env() else ([settings.miniapp_url] if settings.miniapp_url else [])
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Щит на время демо по публичной ссылке (туннель): пока задан
# DEMO_GATE_PASSWORD, весь сайт — мини-апп и админка — закрыт общим
# паролем поверх обычной авторизации. Кука хранит не сам пароль, а его
# хеш, поэтому смена DEMO_GATE_PASSWORD в .env мгновенно разлогинивает всех.
DEMO_GATE_COOKIE = "bh_demo_gate"


def _demo_gate_hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


_DEMO_LOGIN_PAGE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BATTLEHALL — доступ по паролю</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #111; color: #eee;
         display: flex; align-items: center; justify-content: center;
         min-height: 100vh; margin: 0; }}
  form {{ background: #1c1c1c; padding: 32px; border-radius: 12px; width: 280px; }}
  h1 {{ font-size: 18px; margin: 0 0 16px; }}
  input {{ width: 100%; box-sizing: border-box; padding: 10px; margin-bottom: 12px;
           border-radius: 8px; border: 1px solid #333; background: #000; color: #eee; }}
  button {{ width: 100%; padding: 10px; border-radius: 8px; border: none;
            background: #e63946; color: #fff; font-weight: 600; }}
  .error {{ color: #e63946; font-size: 13px; margin: -6px 0 12px; }}
</style>
<form method="post" action="/__demo_login">
  <h1>BATTLEHALL — тестовый доступ</h1>
  {error_html}
  <input type="hidden" name="redirect" value="{redirect}">
  <input type="password" name="password" placeholder="Пароль" autofocus>
  <button type="submit">Войти</button>
</form>
"""


if settings.demo_gate_password:
    @app.middleware("http")
    async def demo_gate(request: Request, call_next):
        expected = _demo_gate_hash(settings.demo_gate_password)
        cookie = request.cookies.get(DEMO_GATE_COOKIE)
        if cookie and hmac.compare_digest(cookie, expected):
            return await call_next(request)

        if request.url.path == "/__demo_login" and request.method == "POST":
            # Разбираем тело руками: обычный x-www-form-urlencoded, без
            # python-multipart в зависимостях (request.form() требует его
            # даже для этого простого случая).
            body = (await request.body()).decode("utf-8", errors="ignore")
            form = dict(parse_qsl(body))
            redirect_to = safe_redirect(form.get("redirect"))
            if hmac.compare_digest(form.get("password", ""), settings.demo_gate_password):
                response = RedirectResponse(url=redirect_to, status_code=303)
                response.set_cookie(
                    DEMO_GATE_COOKIE, expected,
                    max_age=60 * 60 * 24 * 30, httponly=True, samesite="lax",
                    secure=not is_local_env(),
                )
                return response
            page = _DEMO_LOGIN_PAGE.format(
                redirect=html.escape(redirect_to, quote=True),
                error_html='<div class="error">Неверный пароль</div>',
            )
            return HTMLResponse(page, status_code=401)

        redirect_to = request.url.path
        if request.url.query:
            redirect_to += f"?{request.url.query}"
        redirect_to = safe_redirect(redirect_to)
        page = _DEMO_LOGIN_PAGE.format(
            redirect=html.escape(redirect_to, quote=True),
            error_html="",
        )
        return HTMLResponse(page, status_code=401)


if is_local_env():
    # В разработке правки css/js должны быть видны после обычного F5.
    # Одного ETag мало: браузер кэширует статику эвристически и продолжает
    # показывать старый файл, из-за чего чинишь несуществующие баги.
    @app.middleware("http")
    async def no_cache_static(request, call_next):
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response


app.include_router(miniapp.router)
app.include_router(webhooks.router)
app.include_router(admin.router)
app.include_router(console.router)
if not is_production():
    app.include_router(console.test_router)
app.include_router(desk.router)
app.include_router(manage.router)


@app.get("/health", tags=["service"])
def health() -> dict:
    return {"status": "ok"}


@app.get("/admin", include_in_schema=False)
def admin_root_redirect() -> RedirectResponse:
    return RedirectResponse(url="/admin/")


# Статика: фирменный слой на /static, панель админа на /admin, мини-апп — на корне.
# Мини-апп смонтирован последним, чтобы не перехватывать /api/*, /admin/* и /static/*.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/admin", StaticFiles(directory=ADMIN_UI_DIR, html=True), name="admin-ui")
app.mount("/", StaticFiles(directory=MINIAPP_DIR, html=True), name="miniapp")
