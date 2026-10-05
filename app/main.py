"""Совместимая точка входа Uvicorn; сборка находится в app.application."""
from .application import create_app

app = create_app()

# Публичные имена прежнего main для существующих скриптов. Новый код импортирует владельца.
from .modules.pricing.schemas import CalcIn, Credit  # noqa: E402, F401
from .modules.surveys.schemas import RequestIn  # noqa: E402, F401
from .modules.surveys.api import create_request  # noqa: E402, F401
from .modules.market.api import market_branches, market_series, market_claims, stats_page  # noqa: E402, F401
from .modules.market.refresh import _refresh_job, _refresh_state, market_stats  # noqa: E402, F401
