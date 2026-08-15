class TradebotError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(TradebotError):
    status_code = 404
    code = "not_found"


class ConflictError(TradebotError):
    status_code = 409
    code = "conflict"


class ValidationError(TradebotError):
    status_code = 422
    code = "validation_error"


class AuthenticationError(TradebotError):
    status_code = 401
    code = "unauthenticated"


class ForbiddenError(TradebotError):
    status_code = 403
    code = "forbidden"


class RateLimitedError(TradebotError):
    status_code = 429
    code = "rate_limited"


class LookAheadError(TradebotError):
    """Decision code reached for data stamped after the clock. Never catch this — a backtest
    that swallows it silently reports fiction."""

    code = "look_ahead"
