from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

REGISTRY = CollectorRegistry()

http_requests = Counter(
    "tradebot_http_requests_total",
    "HTTP requests handled",
    labelnames=("method", "path", "status"),
    registry=REGISTRY,
)

http_latency = Histogram(
    "tradebot_http_request_seconds",
    "HTTP request latency",
    labelnames=("method", "path"),
    registry=REGISTRY,
)

events_recorded = Counter(
    "tradebot_events_total",
    "Domain events recorded",
    labelnames=("domain", "kind", "severity"),
    registry=REGISTRY,
)

sse_subscribers = Gauge(
    "tradebot_sse_subscribers",
    "Live event-stream subscribers",
    registry=REGISTRY,
)


def render() -> bytes:
    return generate_latest(REGISTRY)
