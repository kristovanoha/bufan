from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite

from models import CompanySnapshot


FORECAST_YEARS = 10
DISCOUNT_RATE = 0.10
TERMINAL_GROWTH_RATE = 0.02
MARGIN_OF_SAFETY = 0.25
MAX_GROWTH_RATE = 0.10
MIN_GROWTH_RATE = -0.05


@dataclass(slots=True)
class ValuationResult:
    owner_earnings: float | None = None
    growth_rate: float | None = None
    intrinsic_value_per_share: float | None = None
    buy_under_price: float | None = None
    discount_rate: float = DISCOUNT_RATE
    terminal_growth_rate: float = TERMINAL_GROWTH_RATE
    margin_of_safety: float = MARGIN_OF_SAFETY
    warnings: list[str] = field(default_factory=list)


def _is_usable_number(value: float | None) -> bool:
    return value is not None and isfinite(value)


def estimate_conservative_growth_rate(snapshot: CompanySnapshot) -> float | None:
    growth_values = [
        value
        for value in (snapshot.earnings_growth, snapshot.revenue_growth)
        if _is_usable_number(value)
    ]
    if not growth_values:
        return None

    growth_rate = min(growth_values)
    return min(max(growth_rate, MIN_GROWTH_RATE), MAX_GROWTH_RATE)


def calculate_owner_earnings_valuation(snapshot: CompanySnapshot) -> ValuationResult:
    warnings: list[str] = []
    owner_earnings = snapshot.free_cash_flow
    shares = snapshot.shares_outstanding
    cash = snapshot.cash_and_equivalents
    debt = snapshot.total_debt
    growth_rate = estimate_conservative_growth_rate(snapshot)

    if not _is_usable_number(owner_earnings) or owner_earnings <= 0:
        warnings.append("Owner earnings DCF nelze spočítat bez kladného Free Cash Flow.")
    if not _is_usable_number(shares) or shares <= 0:
        warnings.append("Vnitřní hodnotu na akcii nelze spočítat bez počtu akcií.")
    if not _is_usable_number(cash):
        warnings.append("Vnitřní hodnotu nelze bezpečně upravit o hotovost, protože chybí cash.")
    if not _is_usable_number(debt):
        warnings.append("Vnitřní hodnotu nelze bezpečně upravit o dluh, protože chybí total debt.")
    if growth_rate is None:
        warnings.append("DCF růst nelze odhadnout, protože chybí revenue growth i earnings growth.")

    if warnings:
        return ValuationResult(
            owner_earnings=owner_earnings,
            growth_rate=growth_rate,
            warnings=warnings,
        )

    present_value = 0.0
    for year in range(1, FORECAST_YEARS + 1):
        projected_cash = owner_earnings * ((1 + growth_rate) ** year)
        present_value += projected_cash / ((1 + DISCOUNT_RATE) ** year)

    terminal_cash = owner_earnings * ((1 + growth_rate) ** FORECAST_YEARS) * (1 + TERMINAL_GROWTH_RATE)
    terminal_value = terminal_cash / (DISCOUNT_RATE - TERMINAL_GROWTH_RATE)
    present_value += terminal_value / ((1 + DISCOUNT_RATE) ** FORECAST_YEARS)

    equity_value = present_value + cash - debt
    if equity_value <= 0:
        return ValuationResult(
            owner_earnings=owner_earnings,
            growth_rate=growth_rate,
            warnings=["DCF vychází záporně nebo nulově po započtení hotovosti a dluhu."],
        )

    intrinsic_value = equity_value / shares
    buy_under_price = intrinsic_value * (1 - MARGIN_OF_SAFETY)

    return ValuationResult(
        owner_earnings=owner_earnings,
        growth_rate=growth_rate,
        intrinsic_value_per_share=intrinsic_value,
        buy_under_price=buy_under_price,
        warnings=warnings,
    )
