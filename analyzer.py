from __future__ import annotations

from models import AnalysisResult, CompanySnapshot, MetricResult
from scoring import score_company
from valuation import calculate_owner_earnings_valuation


def _warning_for_value(value: float | None, label: str) -> str | None:
    if value is None:
        return f"{label} není k dispozici, zobrazeno jako N/A."
    return None


def analyze_company(snapshot: CompanySnapshot) -> AnalysisResult:
    valuation = calculate_owner_earnings_valuation(snapshot)
    snapshot.owner_earnings = valuation.owner_earnings
    snapshot.intrinsic_value_per_share = valuation.intrinsic_value_per_share
    snapshot.buy_under_price = valuation.buy_under_price
    snapshot.dcf_growth_rate = valuation.growth_rate
    snapshot.dcf_discount_rate = valuation.discount_rate
    snapshot.dcf_terminal_growth_rate = valuation.terminal_growth_rate
    snapshot.margin_of_safety = valuation.margin_of_safety

    current_price = getattr(snapshot, "current_price", None)
    trailing_eps = getattr(snapshot, "trailing_eps", None)
    owner_earnings = getattr(snapshot, "owner_earnings", None)
    intrinsic_value_per_share = getattr(snapshot, "intrinsic_value_per_share", None)
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
            label="Dividend Yield",
            value=snapshot.last_year_dividend_yield,
            unit="percent_points",
            description="Posledni dostupny dividendovy vynos podle Yahoo Finance.",
            warning=_warning_for_value(snapshot.last_year_dividend_yield, "Dividend Yield"),
        ),
        MetricResult(
            label="5Y Average Dividend Yield",
            value=snapshot.five_year_avg_dividend_yield,
            unit="percent_points",
            description="Prumerny dividendovy vynos za poslednich 5 let podle Yahoo Finance.",
            warning=_warning_for_value(snapshot.five_year_avg_dividend_yield, "5Y Average Dividend Yield"),
        ),
        MetricResult(
            label="Owner Earnings",
            value=owner_earnings,
            unit="currency",
            description="Aproximace owner earnings pomocí dostupného Free Cash Flow z účetních dat.",
            warning=_warning_for_value(owner_earnings, "Owner Earnings"),
        ),
        MetricResult(
            label="Intrinsic Value",
            value=intrinsic_value_per_share,
            unit="currency_decimal",
            description="Odhad vnitřní hodnoty na akcii pomocí konzervativního owner earnings DCF.",
            warning=_warning_for_value(intrinsic_value_per_share, "Intrinsic Value"),
        ),
        MetricResult(
            label="Buy Under Price",
            value=buy_under_price,
            unit="currency_decimal",
            description="Bezpečná nákupní cena: vnitřní hodnota snížená o 25% margin of safety.",
            warning=_warning_for_value(buy_under_price, "Buy Under Price"),
        ),
        MetricResult(
            label="DCF Growth Rate",
            value=snapshot.dcf_growth_rate,
            unit="percent",
            description="Konzervativní růst použitý v DCF: nižší z revenue growth a earnings growth, ořezaný na -5 % až 10 %.",
            warning=_warning_for_value(snapshot.dcf_growth_rate, "DCF Growth Rate"),
        ),
        MetricResult(
            label="DCF Discount Rate",
            value=snapshot.dcf_discount_rate,
            unit="percent",
            description="Diskontní sazba použitá v DCF modelu.",
            warning=_warning_for_value(snapshot.dcf_discount_rate, "DCF Discount Rate"),
        ),
        MetricResult(
            label="Margin of Safety",
            value=snapshot.margin_of_safety,
            unit="percent",
            description="Bezpečnostní sleva od vnitřní hodnoty pro nákupní cenu.",
            warning=_warning_for_value(snapshot.margin_of_safety, "Margin of Safety"),
        ),
        MetricResult(
            label="Trailing EPS",
            value=trailing_eps,
            unit="currency_decimal",
            description="Historický zisk na akcii podle Yahoo Finance nebo dopočtený z ceny a P/E.",
            warning=_warning_for_value(trailing_eps, "Trailing EPS"),
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
            description="Meziroční růst tržeb z posledních dostupných ročních dat.",
            warning=_warning_for_value(snapshot.revenue_growth, "Revenue Growth"),
        ),
        MetricResult(
            label="Earnings Growth",
            value=snapshot.earnings_growth,
            unit="percent",
            description="Meziroční růst zisku z posledních dostupných ročních dat.",
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
            label="Shares Outstanding",
            value=snapshot.shares_outstanding,
            description="Počet akcií použitý pro přepočet vnitřní hodnoty na akcii.",
            warning=_warning_for_value(snapshot.shares_outstanding, "Shares Outstanding"),
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
            description="Celkové tržby z posledního dostupného ročního výkazu.",
            warning=_warning_for_value(snapshot.total_revenue, "Total Revenue"),
        ),
        MetricResult(
            label="Net Income",
            value=snapshot.net_income,
            unit="currency",
            description="Čistý zisk z posledního dostupného ročního výkazu.",
            warning=_warning_for_value(snapshot.net_income, "Net Income"),
        ),
    ]

    score, max_score, verdict = score_company(snapshot)
    summary = (
        "Cena a základní tržní metriky pochází z Yahoo Finance. "
        "Účetní data se berou primárně z Yahoo Finance a chybějící položky u amerických firem doplňuje SEC EDGAR, pokud jsou dostupné. "
        "Vnitřní hodnota je odhad pomocí owner earnings DCF, ne přesná Buffettova kalkulačka."
    )

    warnings = list(snapshot.warnings)
    warnings.extend(valuation.warnings)
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
