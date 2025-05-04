import hashlib
import hmac
import time

from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader

from .context import ServerContext


def verify_access_token(
    ctx: ServerContext = Depends(),
    token: str = Depends(APIKeyHeader(name='x-access-token')),
) -> None:
    api_token = ctx.config.server.api_token
    if token != api_token:
        raise HTTPException(401, 'Invalid token')


async def verify_slack_token(
    req: Request,
    ctx: ServerContext = Depends(),
    token: str = Depends(APIKeyHeader(name='x-slack-signature')),
    timestamp: str = Depends(APIKeyHeader(name='x-slack-request-timestamp')),
) -> None:
    if abs(time.time() - int(timestamp)) > 5 * 60:
        # The request timestamp is more than five minutes from local time.
        # It could be a replay attack, so let's ignore it.
        raise HTTPException(403, 'Forbidden')

    body = (await req.body()).decode()
    sig_basestring = f'v0:{timestamp}:{body}'.encode()
    sign_key = ctx.config.gold.slack_signing_secret.encode()
    my_signature = hmac.digest(sign_key, sig_basestring, hashlib.sha256).hex()
    if not hmac.compare_digest(token, f'v0={my_signature}'):
        raise HTTPException(401, 'Invalid token')
