from tradebot.core.logging import REDACTED, redact_processor


def _scrub(**fields: object) -> dict[str, object]:
    return redact_processor(None, "info", dict(fields))


def test_sensitive_keys_are_replaced() -> None:
    result = _scrub(api_key="sk-live-secret", password="hunter2", event="stored")
    assert result["api_key"] == REDACTED
    assert result["password"] == REDACTED
    assert result["event"] == "stored"


def test_nested_structures_are_scrubbed() -> None:
    result = _scrub(payload={"credentials": {"token": "abc"}, "symbol": "NVDA"})
    payload = result["payload"]
    assert isinstance(payload, dict)
    assert payload["credentials"] == REDACTED
    assert payload["symbol"] == "NVDA"


def test_lists_are_scrubbed() -> None:
    result = _scrub(items=[{"secret": "x"}, {"symbol": "BTC"}])
    items = result["items"]
    assert isinstance(items, list)
    assert items[0]["secret"] == REDACTED
    assert items[1]["symbol"] == "BTC"


def test_bearer_tokens_in_free_text_are_scrubbed() -> None:
    result = _scrub(event="calling with Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig")
    assert "eyJhbGciOiJIUzI1NiJ9" not in str(result["event"])
    assert REDACTED in str(result["event"])


def test_key_shaped_strings_in_free_text_are_scrubbed() -> None:
    result = _scrub(event="provider rejected sk-live-9f8e7d6c5b4a3210")
    assert "9f8e7d6c5b4a3210" not in str(result["event"])
