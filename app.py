from __future__ import annotations

import os
import time
from threading import Lock, Thread
from pathlib import Path

import pandas as pd
import streamlit as st

from analyzer import analyze_company
from company_loader import load_companies
from data_provider import load_company_snapshot
from fred_provider import fetch_macro_dashboard


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


def ensure_session_state() -> None:
    st.session_state.setdefault("single_analysis", None)
    st.session_state.setdefault("single_ticker", "")
    st.session_state.setdefault("macro_last_completed_at", "")


@st.cache_resource
def get_batch_state() -> dict:
    return {
        "lock": Lock(),
        "running": False,
        "results": [],
        "total": 0,
        "done": 0,
        "status": "",
        "updated_at": "",
        "error": None,
    }


@st.cache_resource
def get_macro_state() -> dict:
    return {
        "lock": Lock(),
        "running": False,
        "results": [],
        "errors": [],
        "status": "",
        "updated_at": "",
        "api_key": "",
        "done": 0,
        "total": 0,
        "started_at": "",
    }


def build_batch_row(analysis) -> dict[str, str]:
    company = analysis.company
    current_price = getattr(company, "current_price", None)
    intrinsic_value = getattr(company, "intrinsic_value_per_share", None)
    buy_under_price = getattr(company, "buy_under_price", None)

    if current_price is not None and buy_under_price is not None and current_price != 0:
        upside_pct = ((buy_under_price - current_price) / current_price) * 100
        price_gap = f"{upside_pct:.2f} %"
        valuation = "Pod nakupni cenou" if buy_under_price >= current_price else "Nad nakupni cenou"
    else:
        price_gap = "N/A"
        valuation = "N/A"

    score_text = "N/A" if analysis.score is None else f"{analysis.score}/{analysis.max_score}"
    if valuation == "Pod nakupni cenou" and analysis.score is not None and analysis.score / analysis.max_score >= 0.75:
        signal = "Silny kandidat"
    elif valuation == "Pod nakupni cenou":
        signal = "Levne, proverit kvalitu"
    elif analysis.score is not None and analysis.score / analysis.max_score >= 0.75:
        signal = "Kvalitni, cekat na cenu"
    else:
        signal = "Sledovat"

    return {
        "Ticker": company.ticker,
        "Firma": company.company_name,
        "Aktualni cena": format_value(current_price, "currency_decimal", company.currency),
        "Vnitrni hodnota": format_value(intrinsic_value, "currency_decimal", company.currency),
        "Nakupni cena": format_value(buy_under_price, "currency_decimal", company.currency),
        "Buffett Score": score_text,
        "Signal": signal,
        "Valuace": valuation,
        "Rozdil k nakupni cene": price_gap,
        "Varovani": str(len(analysis.warnings)),
    }


def style_batch_results(frame: pd.DataFrame):
    def row_style(row):
        if row.get("Valuace") == "Pod nakupni cenou":
            return ["background-color: #eef9f0; color: #000000"] * len(row)
        return [""] * len(row)

    return frame.style.apply(row_style, axis=1)


def build_failed_batch_row(company, error: Exception) -> dict[str, str]:
    return {
        "Ticker": company.ticker,
        "Firma": company.name,
        "Aktualni cena": "N/A",
        "Vnitrni hodnota": "N/A",
        "Nakupni cena": "N/A",
        "Buffett Score": "N/A",
        "Signal": "Chyba analyzy",
        "Valuace": "N/A",
        "Rozdil k nakupni cene": "N/A",
        "Varovani": f"1: {error}",
    }


def start_batch_analysis(companies) -> bool:
    state = get_batch_state()
    with state["lock"]:
        if state["running"]:
            return False
        state["running"] = True
        state["results"] = []
        state["total"] = len(companies)
        state["done"] = 0
        state["status"] = "Pripravuji analyzu..."
        state["updated_at"] = ""
        state["error"] = None

    def worker() -> None:
        try:
            for index, company in enumerate(companies, start=1):
                with state["lock"]:
                    state["status"] = f"Analyzuji {company.ticker}..."

                try:
                    snapshot = load_company_snapshot(company.ticker)
                    analysis = analyze_company(snapshot)
                    row = build_batch_row(analysis)
                except Exception as exc:
                    row = build_failed_batch_row(company, exc)

                with state["lock"]:
                    state["results"].append(row)
                    state["done"] = index

            with state["lock"]:
                state["running"] = False
                state["status"] = "Hromadna analyza dokoncena."
                state["updated_at"] = pd.Timestamp.now().strftime("%d.%m.%Y %H:%M")
        except Exception as exc:
            with state["lock"]:
                state["running"] = False
                state["error"] = str(exc)
                state["status"] = "Hromadna analyza se zastavila."

    Thread(target=worker, daemon=True).start()
    return True


def render_score_explanation() -> None:
    st.subheader("Jak cist Buffett Score")
    st.write(
        "Buffett Score v teto aplikaci neni oficialni Buffettuv ukazatel. "
        "Je to prakticky kontrolni seznam, ktery prevadi Buffettovy principy "
        "do nekolika srozumitelnych bodu nad daty z Yahoo Finance."
    )

    st.markdown(
        "Kazdy bod odpovida jedne otazce:\n"
        "- Je firma ziskova a kapitalove silna?\n"
        "- Ma rozumne zadluzeni?\n"
        "- Premenuje ucetni zisk na skutecnou hotovost?\n"
        "- Roste aspon opatrne smysluplnym tempem?\n"
        "- Neni jeji aktualni cena prilis vysoko oproti odhadovane vnitrni hodnote?"
    )

    st.subheader("Jednotlive body skore")
    checks = [
        ("ROE >= 15 %", "Firma umi dobre vydelavat na vlastnim kapitalu. Vyssi ROE casto znaci kvalitni byznys."),
        ("Debt/Equity <= 100", "Dluh neni prehnany vuci vlastnimu kapitalu. Buffett ma obecne rad firmy, ktere nejsou zavisle na vysokem zadluzeni."),
        ("Operating Margin >= 15 %", "Byznys ma slusnou provozni marzi. To naznacuje cenovou silu nebo efektivni provoz."),
        ("Net Margin >= 10 %", "Firma si z trzeb nechava rozumnou cast jako cisty zisk."),
        ("Free Cash Flow > 0", "Podnik vytvari skutecnou volnou hotovost, nejen ucetni zisk."),
        ("FCF / Net Income >= 75 %", "Zisk se ve velke mire meni v hotovost. To je dulezite, protoze papirovy zisk bez cash flow muze byt slabsi kvality."),
        ("Conservative growth >= 0 %", "Konzervativni rust pouzity v DCF neni zaporny. Nehledame prestreleny optimismus, ale nechceme ani byznys v zjevne erozi."),
        ("Current price <= buy-under price", "Aktualni cena je pod bezpecnou nakupni cenou. Tady se spojuje kvalita firmy a cenova disciplina."),
    ]

    for title, body in checks:
        metric_caption(title, body)

    st.subheader("Jak vznika vysledne cislo")
    st.write(
        "Skore se pocita jen z bodu, pro ktere mame dostupna data. "
        "Kdyz nektera data chybi, aplikace je nevymysli, ale dany bod proste nehodnoti. "
        "Proto muzes videt treba `6/8`, `5/7` nebo `4/6`."
    )

    st.subheader("Jak pocitam vnitrni hodnotu")
    st.write(
        "Vnitrni hodnota se v aplikaci pocita metodou owner earnings DCF, kde jako zaklad pouzivam "
        "aktualni `Free Cash Flow` z Yahoo Finance. Nejde o presnou predpoved budoucnosti, ale o "
        "konzervativni odhad zalozeny na dnes dostupnych datech."
    )
    st.markdown(
        "Postup vypoctu:\n"
        "1. Jako owner earnings beru `Free Cash Flow`.\n"
        "2. Rust odhaduji konzervativne jako nizsi z dvojice `earnings growth` a `revenue growth`.\n"
        "3. Tento rust omezuji do pasma od `-5 %` do `+10 %`, aby model nebyl prestreleny.\n"
        "4. Nasledujicich `10 let` projektuji budoucni cash flow a diskontuji je sazbou `10 %` rocne.\n"
        "5. Po desatem roce pocitam terminalni hodnotu s dlouhodobym rustem `2 %`.\n"
        "6. K soucasne hodnote cash flow pripocitam `cash` a odectu `total debt`.\n"
        "7. Vyslednou hodnotu vlastniho kapitalu vydelim poctem akcii `shares outstanding`, a tim vznikne vnitrni hodnota na akcii."
    )
    st.caption(
        "Pokud chybi Free Cash Flow, rust, cash, total debt nebo pocet akcii, aplikace vnitrni hodnotu "
        "radsi nevypocita a zobrazi `N/A` i varovani."
    )

    st.subheader("Jak pocitam nakupni cenu")
    st.write(
        "Nakupni cena je odvozena primo z vnitrni hodnoty. Aplikace pouziva `25 % margin of safety`, "
        "aby byla hranice pro pripadny nakup opatrnejsi."
    )
    st.markdown(
        "`Nakupni cena = vnitrni hodnota na akcii x (1 - 0.25)`\n\n"
        "Jinymi slovy: pokud vyjde vnitrni hodnota 100 USD na akcii, bezpecnejsi nakupni cena v aplikaci bude 75 USD."
    )

    st.subheader("Jak cist verdikt")
    verdicts = [
        ("Silny Buffett-style profil", "Firma splnila vetsinu dostupnych kvalitativnich i cenovych podminek."),
        ("Kvalitni, cekat na cenu", "Byznys vypada dobre, ale aktualni cena je nad bezpecnou nakupni hranici."),
        ("Dobry Buffett-style profil", "Firma ma vic pozitiv nez negativ, ale neni to uplne cisty kandidat."),
        ("Smiseny Buffett-style profil", "Nektere veci vypadaji dobre a jine uz mene presvedcive."),
        ("Slabsi Buffett-style profil", "Firma podle dostupnych dat neodpovida moc dobre konzervativnimu Buffett-style filtru."),
    ]

    for title, body in verdicts:
        metric_caption(title, body)

    st.subheader("Dulezita poznamka")
    st.write(
        "Vysoke skore samo o sobe neznamena automaticky koupit. "
        "Je to filtr, ktery ma pomoct rychle oddelit silnejsi kandidaty od slabsich. "
        "Finalni rozhodnuti by melo vzdy zohlednit i byznys model firmy, odvetvi, konkurencni vyhodu a tvoji vlastni investicni strategii."
    )


def render_single_analysis(analysis) -> None:
    company = analysis.company
    current_price = getattr(company, "current_price", None)
    intrinsic_value = getattr(company, "intrinsic_value_per_share", None)
    buy_under_price = getattr(company, "buy_under_price", None)
    hero1, hero2, hero3, hero4, hero5, hero6 = st.columns(6)
    hero1.metric("Spolecnost", company.company_name)
    hero2.metric("Ticker", company.ticker)
    hero3.metric("Aktualni cena", format_value(current_price, "currency_decimal", company.currency))
    hero4.metric("Vnitrni hodnota", format_value(intrinsic_value, "currency_decimal", company.currency))
    hero5.metric("Nakupni cena", format_value(buy_under_price, "currency_decimal", company.currency))
    hero6.metric(
        "Buffett Score",
        "N/A" if analysis.score is None else f"{analysis.score}/{analysis.max_score}",
        analysis.verdict,
    )

    info1, info2, info3, info4 = st.columns(4)
    info1.metric("Mena", company.currency or "N/A")
    info2.metric("Sektor", company.sector or "N/A")
    info3.metric("Odvetvi", company.industry or "N/A")
    info4.metric("Market Cap", format_value(company.market_cap, "currency", company.currency))

    st.markdown(
        "<p class='compact-note'>Vnitrni hodnota je owner earnings DCF z Free Cash Flow. "
        "Nakupni cena je vnitrni hodnota snizena o 25% margin of safety. "
        "Nejde o investicni doporuceni a pri chybejicich datech se zobrazi N/A.</p>",
        unsafe_allow_html=True,
    )

    if analysis.warnings:
        with st.expander(f"Varovani a chybejici data ({len(analysis.warnings)})", expanded=False):
            for warning in analysis.warnings:
                st.warning(warning)

    overview_tab, metrics_tab = st.tabs(["Prehled", "Metriky"])

    with overview_tab:
        a1, a2, a3, a4 = st.columns(4)
        metric_map = {metric.label: metric for metric in analysis.metrics}
        a1.metric("ROE", format_value(metric_map["ROE"].value, "percent", company.currency))
        a2.metric("Debt/Equity", format_value(metric_map["Debt/Equity"].value))
        a3.metric("Operating Margin", format_value(metric_map["Operating Margin"].value, "percent", company.currency))
        a4.metric("Free Cash Flow", format_value(metric_map["Free Cash Flow"].value, "currency", company.currency))
        b1, b2 = st.columns(2)
        with b1:
            metric_caption("ROE", "Vynosnost vlastniho kapitalu. Vyssi a stabilni hodnota obvykle znaci kvalitni byznys.")
            metric_caption("Debt/Equity", "Pomer dluhu k vlastnimu kapitalu. Nizsi hodnota obvykle znamena mensi zadluzeni.")
            metric_caption("Operating Margin", "Jak velka cast trzeb zustane po provoznich nakladech. Vyssi marze znaci silnejsi byznys.")
        with b2:
            metric_caption("Free Cash Flow", "Hotovost, ktera firme zbude po provozu a investicich. Pro dlouhodobou kvalitu je dulezita.")
            metric_caption("Trailing P/E", "Pomer aktualni ceny akcie k historickemu zisku na akcii.")
            metric_caption("Nakupni cena", "Vnitrni hodnota snizena o 25% margin of safety. Zelena v hromadne tabulce znamena, ze cena je pod touto hranici.")
        st.write(analysis.summary)

    with metrics_tab:
        st.dataframe(
            metrics_dataframe(analysis.metrics, company.currency),
            use_container_width=True,
            hide_index=True,
            height=620,
        )


def read_batch_state() -> dict:
    state = get_batch_state()
    with state["lock"]:
        return {
            "running": state["running"],
            "results": list(state["results"]),
            "total": state["total"],
            "done": state["done"],
            "status": state["status"],
            "updated_at": state["updated_at"],
            "error": state["error"],
        }


def render_batch_analysis() -> None:
    state = read_batch_state()
    results = state["results"]
    updated_at = state["updated_at"]

    st.subheader("Hromadna analyza seznamu")
    if state["running"]:
        total = max(state["total"], 1)
        st.progress(state["done"] / total, text=f"{state['status']} Hotovo {state['done']}/{state['total']}.")
        st.info("Hromadna analyza bezi na pozadi. Muzes prepnout na analyzu jedne firmy a batch bude pokracovat.")
    if state["error"]:
        st.warning(state["error"])
    if updated_at:
        st.caption(f"Posledni dokoncena hromadna analyza: {updated_at}")
    if not results:
        st.info("Zatim tu neni hromadna analyza. V horni casti teto zalozky klikni na `Analyzovat vse`.")
        if state["running"]:
            time.sleep(2)
            st.rerun()
        return

    st.dataframe(
        style_batch_results(pd.DataFrame(results)),
        use_container_width=True,
        hide_index=True,
        height=900,
    )

    if state["running"]:
        time.sleep(2)
        st.rerun()


def format_macro_value(value: float | None, units: str) -> str:
    if value is None:
        return "N/A"

    units_lower = units.lower()
    if "percent" in units_lower:
        return f"{value:.2f} %"
    if "million" in units_lower:
        return f"{value:,.0f} mil. USD"
    if "billion" in units_lower:
        return f"{value:,.2f} mld."
    if "index" in units_lower:
        return f"{value:,.2f}"
    return f"{value:,.2f}"


def render_macro_series_card(series_result) -> None:
    latest_date = (
        series_result.latest_date.strftime("%d.%m.%Y")
        if series_result.latest_date is not None
        else "N/A"
    )
    chart_frame = series_result.observations.rename(columns={"value": series_result.definition.title})

    st.markdown(f"### {series_result.definition.title}")
    metric1, metric2 = st.columns(2)
    metric1.metric("Posledni hodnota", format_macro_value(series_result.latest_value, series_result.units))
    metric2.metric("Datum posledni hodnoty", latest_date)
    st.line_chart(chart_frame, use_container_width=True, height=260)
    st.caption(series_result.definition.description)
    st.caption(
        f"Serie: {series_result.definition.series_id} | Jednotky: {series_result.units} | Frekvence: {series_result.frequency}"
    )
    if series_result.notes:
        with st.expander("Poznamka k serii", expanded=False):
            st.write(series_result.notes)


def start_macro_analysis(api_key: str) -> bool:
    state = get_macro_state()
    with state["lock"]:
        if state["running"] and state["api_key"] == api_key:
            return False
        state["running"] = True
        state["results"] = []
        state["errors"] = []
        state["status"] = "Nacitam makro data z FRED..."
        state["updated_at"] = ""
        state["api_key"] = api_key
        state["done"] = 0
        state["total"] = 15
        state["started_at"] = pd.Timestamp.now().strftime("%d.%m.%Y %H:%M:%S")

    def worker() -> None:
        def on_progress(results, errors, done, total) -> None:
            with state["lock"]:
                state["results"] = results
                state["errors"] = errors
                state["done"] = done
                state["total"] = total
                state["status"] = f"Nacteno {done}/{total} makro serii..."

        results, errors = fetch_macro_dashboard(api_key, progress_callback=on_progress)
        with state["lock"]:
            state["results"] = results
            state["errors"] = errors
            state["running"] = False
            state["status"] = "Makro data nactena."
            state["updated_at"] = pd.Timestamp.now().strftime("%d.%m.%Y %H:%M")
            state["done"] = len(results) + len(errors)

    Thread(target=worker, daemon=True).start()
    return True


def ensure_macro_preload_started() -> None:
    api_key = st.secrets.get("FRED_API_KEY", "") or os.getenv("FRED_API_KEY", "")
    if not api_key:
        return

    state = read_macro_state()
    if state["running"]:
        return
    if state["api_key"] == api_key and (state["results"] or state["errors"]):
        return

    start_macro_analysis(api_key)


def read_macro_state() -> dict:
    state = get_macro_state()
    with state["lock"]:
        return {
            "running": state["running"],
            "results": list(state["results"]),
            "errors": list(state["errors"]),
            "status": state["status"],
            "updated_at": state["updated_at"],
            "api_key": state["api_key"],
            "done": state["done"],
            "total": state["total"],
            "started_at": state["started_at"],
        }


def render_macro_sections(results) -> None:
    categories = [
        "Menova politika a inflace",
        "Realna ekonomika",
        "Penezni zasoba a dluh",
    ]

    for category in categories:
        st.markdown("---")
        st.subheader(category)
        series_for_category = [item for item in results if item.definition.category == category]
        for index in range(0, len(series_for_category), 2):
            columns = st.columns(2)
            for column, series_result in zip(columns, series_for_category[index : index + 2]):
                with column:
                    render_macro_series_card(series_result)


def render_macro_header() -> None:
    st.subheader("Makroekonomika USA")
    st.write(
        "Tato sekce taha makroekonomicka data z FRED API a zobrazuje je jako samostatne grafy s kratkym vysvetlenim. "
        "Cilem je mit na jednom miste inflaci, sazby, nezamestnanost, HDP, penezni zasobu, dluh i recesni signaly."
    )
    st.caption(
        "Zdroj dat: FRED (Federal Reserve Economic Data). Nacitani probiha na pozadi a samotne FRED serie se stahuji paralelne."
    )


def render_macro_toolbar(state: dict, api_key: str, auto_rerun_on_start: bool = False) -> dict:
    header1, header2 = st.columns([1, 3])
    with header1:
        if st.button("Obnovit makro data", use_container_width=True):
            start_macro_analysis(api_key)
            state = read_macro_state()
            if auto_rerun_on_start:
                st.rerun()
    with header2:
        if state["updated_at"]:
            st.caption(f"Posledni uspesne nacteni: {state['updated_at']}")
        elif state["started_at"]:
            st.caption(f"Spusteno: {state['started_at']}")
    return state


@st.fragment(run_every=1)
def render_macro_loading_view(api_key: str) -> None:
    state = read_macro_state()
    state = render_macro_toolbar(state, api_key)

    if state["running"]:
        total = max(state["total"], 1)
        st.progress(state["done"] / total, text=state["status"])
        st.info("Makro data se nacitaji na pozadi. Hotove grafy se postupne doplnuji.")
        if state["errors"]:
            for error in state["errors"]:
                st.warning(error)
        if state["results"]:
            render_macro_sections(state["results"])
        return

    if state["updated_at"] and st.session_state.macro_last_completed_at != state["updated_at"]:
        st.session_state.macro_last_completed_at = state["updated_at"]
        st.rerun()

    if state["errors"] and not state["results"]:
        for error in state["errors"]:
            st.warning(error)
        return

    if state["results"]:
        render_macro_sections(state["results"])


def render_macro_analysis() -> None:
    render_macro_header()
    api_key = st.secrets.get("FRED_API_KEY", "") or os.getenv("FRED_API_KEY", "")
    if not api_key:
        st.info(
            "Pro nacitani makro dat chybi ulozeny FRED API key ve Streamlit secrets nebo v `FRED_API_KEY`."
        )
        return

    state = read_macro_state()
    key_changed = state["api_key"] != api_key
    if key_changed or (not state["running"] and not state["results"] and not state["errors"]):
        start_macro_analysis(api_key)
        st.session_state.macro_last_completed_at = ""
        state = read_macro_state()

    if state["running"]:
        render_macro_loading_view(api_key)
        return

    state = render_macro_toolbar(state, api_key, auto_rerun_on_start=True)

    if state["errors"] and not state["results"]:
        for error in state["errors"]:
            st.warning(error)
        return

    if state["errors"]:
        for error in state["errors"]:
            st.warning(error)

    if not state["results"]:
        st.warning("Nepodarilo se nacist zadna makro data z FRED.")
        return

    render_macro_sections(state["results"])


def render_placeholder_tab(title: str) -> None:
    st.subheader(title)
    st.info("Tato sekce je zatim pripravena pro dalsi vypocty a rozsireni.")
    st.caption("Tato zalozka je zatim prazdna a pripravena pro samostatnou analyzu.")


def main() -> None:
    st.title("Buffett Analyzer")
    st.caption("Osobni fundamentalni analyza USA akcii inspirovana principy Warrena Buffetta.")
    ensure_session_state()
    ensure_macro_preload_started()

    companies = load_companies(Path(__file__).with_name("companies.txt"))
    company_options = {f"{company.ticker} | {company.name}": company.ticker for company in companies}
    buffett_tab, macro_tab, future_tab_one, future_tab_two = st.tabs(
        ["Buffett analyza", "Makroekonomika (FRED)", "Dalsi analyza 1", "Dalsi analyza 2"]
    )

    with buffett_tab:
        st.subheader("Vyber akcie")
        control1, control2, control3, control4 = st.columns([2.2, 1.3, 1, 1])
        with control1:
            st.markdown("**Firma ze seznamu**")
            selected_label = st.selectbox(
                "Vyber firmu ze seznamu",
                options=list(company_options.keys()) if company_options else [],
                index=0 if company_options else None,
                placeholder="Nejprve dopln companies.txt",
                label_visibility="collapsed",
            )
        with control2:
            st.markdown("**Rucni ticker**")
            manual_ticker = st.text_input(
                "Nebo zadej ticker rucne",
                placeholder="Napr. AAPL",
                label_visibility="collapsed",
            ).strip().upper()
        with control3:
            st.markdown("**Akce**")
            analyze_clicked = st.button("Analyzovat", type="primary", use_container_width=True)
        with control4:
            st.markdown("**Akce**")
            analyze_all_clicked = st.button("Analyzovat vse", use_container_width=True)

        st.caption("Zdroj dat: Yahoo Finance pres knihovnu yfinance.")
        selected_ticker = manual_ticker or company_options.get(selected_label, "")
        main_tab, batch_tab, score_tab = st.tabs(
            ["Analyza firmy", "Hromadna analyza", "Jak funguje Buffett Score"]
        )

        with main_tab:
            if analyze_clicked:
                if not selected_ticker:
                    st.warning("Vyber ticker ze seznamu nebo ho zadej rucne.")
                else:
                    with st.spinner(f"Nacitam data pro {selected_ticker}..."):
                        snapshot = load_company_snapshot(selected_ticker)
                        st.session_state.single_analysis = analyze_company(snapshot)
                        st.session_state.single_ticker = selected_ticker

            if st.session_state.single_analysis is None:
                st.info("Zatim tu neni analyza konkretni firmy. Vyber ticker vyse a klikni na `Analyzovat`.")
            else:
                render_single_analysis(st.session_state.single_analysis)

        with batch_tab:
            if analyze_all_clicked:
                if not companies:
                    st.warning("Seznam firem je prazdny. Nejprve dopln `companies.txt`.")
                else:
                    started = start_batch_analysis(companies)
                    if started:
                        st.success("Hromadna analyza se spustila na pozadi.")
                    else:
                        st.info("Hromadna analyza uz bezi.")

            render_batch_analysis()

        with score_tab:
            render_score_explanation()

    with future_tab_one:
        render_placeholder_tab("Dalsi analyza 1")

    with future_tab_two:
        render_placeholder_tab("Dalsi analyza 2")

    with macro_tab:
        render_macro_analysis()


if __name__ == "__main__":
    main()
