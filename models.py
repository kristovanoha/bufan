from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Company:
    ticker: str
    name: str


@dataclass(slots=True)
class MetricResult:
    label: str
    value: float | str | None
    unit: str = ""
    description: str = ""
    warning: str | None = None


@dataclass(slots=True)
class CompanySnapshot:
    ticker: str
    company_name: str
    sector: str | None = None
    industry: str | None = None
    currency: str | None = None
    current_price: float | None = None
    market_cap: float | None = None
    shares_outstanding: float | None = None
    trailing_pe: float | None = None
    trailing_eps: float | None = None
    owner_earnings: float | None = None
    intrinsic_value_per_share: float | None = None
    buy_under_price: float | None = None
    dcf_growth_rate: float | None = None
    dcf_discount_rate: float | None = None
    dcf_terminal_growth_rate: float | None = None
    margin_of_safety: float | None = None
    current_ratio: float | None = None
    return_on_equity: float | None = None
    debt_to_equity: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None
    revenue_growth: float | None = None
    earnings_growth: float | None = None
    free_cash_flow: float | None = None
    operating_cash_flow: float | None = None
    capital_expenditures: float | None = None
    total_revenue: float | None = None
    net_income: float | None = None
    total_debt: float | None = None
    stockholders_equity: float | None = None
    cash_and_equivalents: float | None = None
    source_notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raw_info: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AnalysisResult:
    company: CompanySnapshot
    metrics: list[MetricResult]
    score: int | None
    max_score: int
    verdict: str
    summary: str
    warnings: list[str] = field(default_factory=list)
