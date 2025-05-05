from fastapi import APIRouter, Depends

from marketbot.context import ServerContext

# The root router
router = APIRouter()


@router.post(
    path='/gold-price',
    summary="Get the latest Gold price",
)
async def slack_command(ctx: ServerContext = Depends()):
    prices = ctx.gold.get_latest_gold_prices()
    return ctx.gold.build_slack_message(prices)
