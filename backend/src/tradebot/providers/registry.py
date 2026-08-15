from collections.abc import Iterator

from tradebot.providers.base import (
    AssetClass,
    Capability,
    Provider,
    ProviderConfig,
)

_REGISTRY: dict[str, type[Provider]] = {}


def register(provider_cls: type[Provider]) -> type[Provider]:
    key = getattr(provider_cls, "key", "")
    if not key:
        raise ValueError(f"{provider_cls.__name__} must declare a key")
    if key in _REGISTRY and _REGISTRY[key] is not provider_cls:
        raise ValueError(f"provider key already registered: {key}")
    _REGISTRY[key] = provider_cls
    return provider_cls


def registered() -> dict[str, type[Provider]]:
    return dict(_REGISTRY)


def get(key: str) -> type[Provider]:
    try:
        return _REGISTRY[key]
    except KeyError as exc:
        raise KeyError(f"unknown provider: {key}") from exc


def describe() -> list[dict[str, object]]:
    """The catalogue a settings screen renders: what exists and what each one needs."""
    return [
        {
            "key": cls.key,
            "label": cls.label,
            "capabilities": sorted(c.value for c in cls.capabilities),
            "asset_classes": sorted(a.value for a in cls.asset_classes),
            "credential_fields": [
                {"name": f.name, "label": f.label, "required": f.required}
                for f in cls.credential_fields
            ],
            "rate_limits": {
                "requests_per_minute": cls.rate_limits.requests_per_minute,
                "requests_per_day": cls.rate_limits.requests_per_day,
                "max_concurrency": cls.rate_limits.max_concurrency,
            },
        }
        for cls in sorted(_REGISTRY.values(), key=lambda c: c.default_priority)
    ]


def build(key: str, config: ProviderConfig | None = None) -> Provider:
    return get(key)(config)


def for_capability(
    capability: Capability, asset_class: AssetClass | None = None
) -> Iterator[type[Provider]]:
    for cls in sorted(_REGISTRY.values(), key=lambda c: c.default_priority):
        if capability not in cls.capabilities:
            continue
        if asset_class is not None and asset_class not in cls.asset_classes:
            continue
        yield cls
