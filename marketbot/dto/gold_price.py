from dataclasses import dataclass


@dataclass()
class GoldPriceResult:
    name: str
    link: str
    price: float
    change: float = 0
