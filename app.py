from __future__ import annotations

from pathlib import Path

import streamlit as st
import pandas as pd

from analyzer import analyze_company
from company_loader import load_companies
from data_provider import load_company_snapshot


st.set_page_config(page_title="Buffett Analyzer", layout="wide")

st.markdown(
    """
    <style>
    .block-container {
        padding-top: 1.2rem;
        padding-bottom: 1rem;
        max-width: 1400px;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.15rem;
    }
    div[data-testid="stMetricLabel"] {
        font-size: 0.8rem;
    }
    .compact-note {
        font-size: 0.88rem;
        color: #4b5563;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def format_value(value: float | str | None, unit: str = "", currency: str | None = None) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, str):
        return value
    if unit == "percent":
        return f"{value * 100:.2f} %"
    if unit == "currency":
        suffix = f" {currency}" if currency else ""
        return f"{value:,.0f}{suffix}"
    if unit == "currency_decimal":
        suffix = f" {currency}" if currency else ""
        return f"{value:,.2f}{suffix}"
    return f"{value:,.2f}"


def metrics_dataframe(metrics, currency: str | None) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Metric": metric.label,
                "Value": format_value(metric.value, metric.unit, currency),
                "Description": metric.description,
            }
            for metric in metrics
        ]
    )


def metric_caption(title: str, body: str) -> None:
    st.markdown(f"**{title}**  \n{body}")


def build_batch_row(analysis) -> dict[str, str]:
    company = analysis.company
    current_price = getattr(company, "current_price", None)
    fair_price = getattr(company, "buy_under_price", None)

    if current_price is not None and fair_price is not None and current_price != 0:
        upside_pct = ((fair_price - current_price) / current_price) * 100
        price_gap = f"{upside_pct:.2f} %"
        valuation = "Pod férovou cenou" if fair_price >= current_price else "Nad férovou cenou"
    else:
        price_gap = "N/A"
        valuation = "N/A"

    score_text = "N/A" if analysis.score is None else f"{analysis.score}/{analysis.max_score}"

    return {
        "Ticker": company.ticker,
        "Company": company.company_name,
        "Current Price": format_value(current_price, "currency_decimal", company.currency),
        "Fair Buy Price": format_value(fair_price, "currency_decimal", company.currency),
        "Buffett Score": score_text,
        "Valuation": valuation,
        "Gap to Fair Price": price_gap,
        "Warnings": str(len(analysis.warnings)),
    }


def style_batch_results(frame: pd.DataFrame):
    def row_style(row):
        if row.get("Valuation") == "Pod férovou cenou":
            return ["background-color: #eef9f0; color: #000000"] * len(row)
        return [""] * len(row)

    return frame.style.apply(row_style, axis=1)


def main() -> None:
    st.title("Buffett Analyzer")
    st.caption("Osobní fundamentální analýza USA akcií inspirovaná principy Warrena Buffetta.")

    companies = load_companies(Path(__file__).with_name("companies.txt"))
    company_options = {f"{company.ticker} - {company.name}": company.ticker for company in companies}

    with st.sidebar:
        st.header("Výběr akcie")
        selected_label = st.selectbox(
            "Vyber firmu ze seznamu",
            options=list(company_options.keys()) if company_options else [],
            index=0 if company_options else None,
            placeholder="Nejprve doplň companies.txt",
        )
        manual_ticker = st.text_input("Nebo zadej ticker ručně", placeholder="Např. AAPL").strip().upper()
        analyze_clicked = st.button("Analyzovat", type="primary", use_container_width=True)
        analyze_all_clicked = st.button("Analyzovat vše", use_container_width=True)
        st.markdown("---")
        st.info("Zdroj dat: Yahoo Finance přes knihovnu yfinance.")

    selected_ticker = manual_ticker or company_options.get(selected_label, "")

    if analyze_all_clicked:
        if not companies:
            st.warning("Seznam firem je prázdný. Nejprve doplň `companies.txt`.")
            return

        st.subheader("Hromadná analýza seznamu")
        st.caption("Tabulka se doplňuje postupně podle toho, jak dobíhá analýza jednotlivých firem.")

        progress_bar = st.progress(0, text="Připravuji analýzu...")
        status_placeholder = st.empty()
        table_placeholder = st.empty()
        results: list[dict[str, str]] = []

        for index, company in enumerate(companies, start=1):
            status_placeholder.write(f"Analyzuji `{company.ticker}`...")
            snapshot = load_company_snapshot(company.ticker)
            analysis = analyze_company(snapshot)
            results.append(build_batch_row(analysis))
            results_frame = pd.DataFrame(results)

            table_placeholder.dataframe(
                style_batch_results(results_frame),
                use_container_width=True,
                hide_index=True,
                height=620,
            )
            progress_bar.progress(
                index / len(companies),
                text=f"Hotovo {index}/{len(companies)} firem",
            )

        status_placeholder.success("Hromadná analýza dokončena.")
        return

    if not selected_ticker:
        st.warning("Vyber ticker ze seznamu nebo ho zadej ručně.")
        return

    if not analyze_clicked:
        st.write("Aplikace je připravená. Klikni na `Analyzovat` pro načtení dat.")
        return

    with st.spinner(f"Načítám data pro {selected_ticker}..."):
        snapshot = load_company_snapshot(selected_ticker)
        analysis = analyze_company(snapshot)

    company = analysis.company
    current_price = getattr(company, "current_price", None)
    buy_under_price = getattr(company, "buy_under_price", None)
    hero1, hero2, hero3, hero4, hero5, hero6 = st.columns(6)
    hero1.metric("Společnost", company.company_name)
    hero2.metric("Ticker", company.ticker)
    hero3.metric("Aktuální cena", format_value(current_price, "currency_decimal", company.currency))
    hero4.metric("Férová cena k nákupu", format_value(buy_under_price, "currency_decimal", company.currency))
    hero5.metric("Trailing P/E", format_value(company.trailing_pe))
    hero6.metric(
        "Buffett Score",
        "N/A" if analysis.score is None else f"{analysis.score}/{analysis.max_score}",
        analysis.verdict,
    )

    info1, info2, info3, info4 = st.columns(4)
    info1.metric("Měna", company.currency or "N/A")
    info2.metric("Sektor", company.sector or "N/A")
    info3.metric("Odvětví", company.industry or "N/A")
    info4.metric("Market Cap", format_value(company.market_cap, "currency", company.currency))

    st.markdown(
        "<p class='compact-note'>Férová cena k nákupu je zde jednoduchý orientační výpočet: 15x trailing EPS. "
        "Není to investiční doporučení a zobrazí se jen tehdy, když jsou dostupná potřebná data.</p>",
        unsafe_allow_html=True,
    )

    if analysis.warnings:
        with st.expander(f"Varování a chybějící data ({len(analysis.warnings)})", expanded=False):
            for warning in analysis.warnings:
                st.warning(warning)

    tab1, tab2, tab3 = st.tabs(["Přehled", "Metriky", "Metodika"])

    with tab1:
        a1, a2, a3, a4 = st.columns(4)
        metric_map = {metric.label: metric for metric in analysis.metrics}
        a1.metric("ROE", format_value(metric_map["ROE"].value, "percent", company.currency))
        a2.metric("Debt/Equity", format_value(metric_map["Debt/Equity"].value))
        a3.metric("Operating Margin", format_value(metric_map["Operating Margin"].value, "percent", company.currency))
        a4.metric("Free Cash Flow", format_value(metric_map["Free Cash Flow"].value, "currency", company.currency))
        b1, b2 = st.columns(2)
        with b1:
            metric_caption("ROE", "Výnosnost vlastního kapitálu. Vyšší a stabilní hodnota obvykle značí kvalitní byznys.")
            metric_caption("Debt/Equity", "Poměr dluhu k vlastnímu kapitálu. Nižší hodnota obvykle znamená menší zadlužení.")
            metric_caption("Operating Margin", "Jak velká část tržeb zůstane po provozních nákladech. Vyšší marže značí silnější byznys.")
        with b2:
            metric_caption("Free Cash Flow", "Hotovost, která firmě zbude po provozu a investicích. Pro dlouhodobou kvalitu je důležitá.")
            metric_caption("Trailing P/E", "Poměr aktuální ceny akcie k historickému zisku na akcii.")
            metric_caption("Férová cena k nákupu", "V této verzi je to orientačně 15x trailing EPS. Slouží jako jednoduchý Buffett-style filtr ceny.")
        st.write(analysis.summary)

    with tab2:
        st.dataframe(
            metrics_dataframe(analysis.metrics, company.currency),
            use_container_width=True,
            hide_index=True,
            height=620,
        )

    with tab3:
        st.markdown(
            "- Aplikace používá pouze data z Yahoo Finance přes `yfinance`.\n"
            "- Chybějící hodnoty nejsou domýšlené a zobrazují se jako `N/A`.\n"
            "- Buffett Score je jednoduché orientační skóre nad dostupnými metrikami kvality.\n"
            "- Férová cena k nákupu je konzervativní pomocný výpočet `15 x trailing EPS`."
        )


if __name__ == "__main__":
    main()
