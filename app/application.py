"""Сборка HTTP-приложения. Предметные модули не импортируют этот файл."""
from importlib import import_module

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import DEFAULT_EXCLUDED_CONTENT_TYPES, GZipMiddleware

from .config import ASSET_DIR, Settings, load_environment

# Владение маршрутами явно видно здесь. Старые реализации переносятся постепенно,
# без изменения адресов, прав доступа и форматов уже работающего клиента.
MODULES = {
    "pricing": ("modules.pricing.api", "min_rates", "product_import", "vehicle_class", "osgor"),
    "surveys": ("modules.surveys.api", "approvals", "proposal", "risk_api", "surveyor_chat"),
    "documents": ("modules.documents.api", "photos", "valuation", "docparse", "ingest",
                  "analysis_docs", "legal", "act", "exports"),
    "identity": ("auth", "registration", "tg_link", "login_links", "google_auth", "staff"),
    "market": ("modules.market.api", "statagency", "uzex", "lawwatch", "market_knowledge", "competitors"),
    "finance": ("modules.finance.api", "finance", "portfolio", "history", "calibration", "claims_import"),
    "platform": ("infrastructure.health", "llm", "deploy", "telegram", "tgbot", "i18n",
                 "team", "knowledge", "office_api", "ui.operations", "ui.pages"),
}


def create_app() -> FastAPI:
    """Один процесс обслуживает одну конфигурацию хранения; база открывается в lifespan."""
    load_environment()
    settings = Settings.from_env()
    from . import guard, web
    from .errors import NotFoundError
    from .infrastructure.lifecycle import lifespan

    web.setup_logging()
    app = FastAPI(
        title="ИИ-сюрвейер INSON", version="0.1",
        description="Расчёт ставки, проверки по законодательству и тарифной политике, документы, аналитика.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    for modules in MODULES.values():
        for name in modules:
            # Ошибка импорта обязательного модуля останавливает запуск.
            app.include_router(import_module(f"app.{name}").router)

    @app.exception_handler(NotFoundError)
    async def not_found(request, exc):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    app.mount("/static", StaticFiles(directory=ASSET_DIR / "static"), name="static")
    guard.install(app)
    from .chatgpt_plan import TesterContextMiddleware
    app.add_middleware(TesterContextMiddleware)
    app.add_middleware(web.CacheControlMiddleware)
    app.add_middleware(web.PageGzipCache, compresslevel=6)
    excluded = DEFAULT_EXCLUDED_CONTENT_TYPES + (
        "application/pdf", "application/octet-stream", "image/*",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6, exclude_content_types=excluded)
    app.add_middleware(web.ErrorMiddleware)
    return app
