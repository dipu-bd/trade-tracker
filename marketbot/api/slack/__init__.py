from fastapi import APIRouter, Depends

from marketbot.context import ServerContext
from marketbot.security import verify_access_token, verify_slack_token

from .events import router as slack_events

# The root router
router = APIRouter()

router.include_router(
    slack_events,
    prefix='/events',
    dependencies=[Depends(verify_slack_token)],
)


@router.post(
    path="/gold-price",
    summary="Send slack alert for latest Gold price",
    dependencies=[Depends(verify_access_token)],
)
def send_gold_price_alert(ctx: ServerContext = Depends()):
    prices = ctx.gold_price.get_latest_gold_prices()
    data = ctx.slack.build_message_for_gold_prices(prices)
    ctx.slack.send_slack_alert(data)
    return 'OK'
