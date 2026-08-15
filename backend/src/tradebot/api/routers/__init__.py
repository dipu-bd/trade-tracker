from fastapi import APIRouter

from tradebot.api.routers import auth, credentials, health, market, observability, portfolios

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(credentials.router)
api_router.include_router(market.router)
api_router.include_router(portfolios.router)
api_router.include_router(observability.router)

__all__ = ["api_router"]
