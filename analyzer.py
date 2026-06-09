from __future__ import annotations

from models import AnalysisResult, CompanySnapshot, MetricResult
from scoring import score_company


def _warning_for_value(value: float | None, label: str) -> str | None:
    if value is None:
        return f"{label} není k dispozici, zobrazeno jako N/A."
    return None


def analyze_company(snapshot: CompanySnapshot) -> AnalysisResult:
    current_price = getattr(snapshot, "current_price", None)
    trailing_eps = getattr(snapshot, "trailing_eps", None)
    buy_under_price = getattr(snapshot, "buy_under_price", None)

    metrics = [
        MetricResult(
            label="Current Price",
            value=current_price,
            unit="currency_decimal",
            description="Aktuální tržní cena podle Yahoo Finance.",
            warning=_warning_for_value(current_price, "Current Price"),
        ),
        MetricResult(
            label="Market Cap",
            value=snapshot.market_cap,
            unit="currency",
            description="Celková tržní hodnota společnosti.",
            warning=_warning_for_value(snapshot.market_cap, "Market Cap"),
        ),
        MetricResult(
            label="Trailing P/E",
            value=snapshot.trailing_pe,
            description="Poměr ceny akcie k historickému zisku.",
            warning=_warning_for_value(snapshot.trailing_pe, "Trailing P/E"),
        ),
        MetricResult(
            label="Trailing EPS",
            value=trailing_eps,
            unit="currency_decimal",
            description="Historický zisk na akcii podle Yahoo Finance nebo dopočtený z ceny a P/E.",
            warning=_warning_for_value(trailing_eps, "Trailing EPS"),
        ),
        MetricResult(
            label="Buy Under Price",
            value=buy_under_price,
            unit="currency_decimal",
            description="Jednoduchá orientační buy-under cena počítaná jako 15x trailing EPS.",
            warning=_warning_for_value(buy_under_price, "Buy Under Price"),
        ),
        MetricResult(
            label="ROE",
            value=snapshot.return_on_equity,
            unit="percent",
            description="Výnosnost vlastního kapitálu.",
            warning=_warning_for_value(snapshot.return_on_equity, "ROE"),
        ),
        MetricResult(
            label="Debt/Equity",
            value=snapshot.debt_to_equity,
            description="Poměr dluhu k vlastnímu kapitálu.",
            warning=_warning_for_value(snapshot.debt_to_equity, "Debt/Equity"),
        ),
        MetricResult(
            label="Operating Margin",
            value=snapshot.operating_margin,
            unit="percent",
            description="Podíl provozního zisku na tržbách.",
            warning=_warning_for_value(snapshot.operating_margin, "Operating Margin"),
        ),
        MetricResult(
            label="Net Margin",
            value=snapshot.net_margin,
            unit="percent",
            description="Podíl čistého zisku na tržbách.",
            warning=_warning_for_value(snapshot.net_margin, "Net Margin"),
        ),
        MetricResult(
            label="Revenue Growth",
            value=snapshot.revenue_growth,
            unit="percent",
            description="Meziroční růst tržeb podle Yahoo Finance.",
            warning=_warning_for_value(snapshot.revenue_growth, "Revenue Growth"),
        ),
        MetricResult(
            label="Earnings Growth",
            value=snapshot.earnings_growth,
            unit="percent",
            description="Meziroční růst zisku podle Yahoo Finance.",
            warning=_warning_for_value(snapshot.earnings_growth, "Earnings Growth"),
        ),
        MetricResult(
            label="Free Cash Flow",
            value=snapshot.free_cash_flow,
            unit="currency",
            description="Volné cash flow společnosti.",
            warning=_warning_for_value(snapshot.free_cash_flow, "Free Cash Flow"),
        ),
        MetricResult(
            label="Operating Cash Flow",
            value=snapshot.operating_cash_flow,
            unit="currency",
            description="Cash flow z provozní činnosti.",
            warning=_warning_for_value(snapshot.operating_cash_flow, "Operating Cash Flow"),
        ),
        MetricResult(
            label="Total Revenue",
            value=snapshot.total_revenue,
            unit="currency",
            description="Celkové tržby z posledního dostupného výkazu.",
            warning=_warning_for_value(snapshot.total_revenue, "Total Revenue"),
        ),
        MetricResult(
            label="Net Income",
            value=snapshot.net_income,
            unit="currency",
            description="Čistý zisk z posledního dostupného výkazu.",
            warning=_warning_for_value(snapshot.net_income, "Net Income"),
        ),
    ]

    score, max_score, verdict = score_company(snapshot)
    summary = (
        "Analýza používá pouze data dostupná z Yahoo Finance přes knihovnu yfinance. "
        "Chybějící hodnoty nejsou dopočítávány a jsou označeny jako N/A."
    )

    warnings = list(snapshot.warnings)
    warnings.extend(metric.warning for metric in metrics if metric.warning)

    unique_warnings = list(dict.fromkeys(warnings))
    return AnalysisResult(
        company=snapshot,
        metrics=metrics,
        score=score,
        max_score=max_score,
        verdict=verdict,
        summary=summary,
        warnings=unique_warnings,
    )
