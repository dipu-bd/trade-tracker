from fastapi import APIRouter, Depends

from marketbot.context import ServerContext
from marketbot.security import verify_access_token

# The root router
router = APIRouter()


@router.get(
    path="/latest",
    summary="Gets the latest gold price",
    dependencies=[Depends(verify_access_token)],
)
def get_latest_prices(ctx: ServerContext = Depends()):
    return ctx.gold.get_latest_gold_prices()
