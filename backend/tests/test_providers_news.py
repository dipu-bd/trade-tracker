from datetime import UTC, datetime

from tradebot.providers.impl.alphavantage import _parse_news


def row(title: str, tickers: list[tuple[str, float]]) -> dict:
    return {
        "title": title,
        "time_published": "20260815T120000",
        "source": "Test",
        "url": "https://example.test/x",
        "summary": "body",
        "ticker_sentiment": [
            {"ticker": ticker, "relevance_score": str(score)} for ticker, score in tickers
        ],
    }


def test_an_article_about_none_of_the_requested_tickers_is_dropped() -> None:
    """Attributing it to the first requested symbol manufactures sentiment from nothing.

    Live check against the real feed found this: a Bitcoin-miner story came back labelled AAPL
    purely because AAPL was first in the request.
    """
    assert (
        _parse_news(row("Unrelated company files S-1", [("TSLA", 0.9)]), ["AAPL", "MSFT"]) is None
    )


def test_an_article_is_attributed_to_its_most_relevant_requested_ticker() -> None:
    """This feed tags several mega-caps in one index-fund article; first-listed is arbitrary."""
    item = _parse_news(row("Index fund update", [("AAPL", 0.31), ("MSFT", 0.88)]), ["AAPL", "MSFT"])

    assert item is not None
    assert item.symbol == "MSFT"


def test_a_missing_relevance_score_does_not_crash_attribution() -> None:
    payload = row("Something", [("AAPL", 0.0)])
    payload["ticker_sentiment"][0]["relevance_score"] = "not-a-number"

    item = _parse_news(payload, ["AAPL"])

    assert item is not None and item.symbol == "AAPL"


def test_an_item_without_a_headline_or_timestamp_is_dropped() -> None:
    assert _parse_news({"title": "", "time_published": "20260815T120000"}, ["AAPL"]) is None
    assert _parse_news({"title": "x", "time_published": "junk"}, ["AAPL"]) is None


def test_the_parsed_item_keeps_its_publication_time() -> None:
    item = _parse_news(row("Headline", [("AAPL", 0.7)]), ["AAPL"])

    assert item is not None
    assert item.published_at == datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
