from __future__ import annotations

import json
import os
import time
from threading import Lock, Thread
from pathlib import Path

import pandas as pd
import streamlit as st

from analyzer import analyze_company
from company_loader import load_companies
from crypto_provider import fetch_crypto_dashboard
from czech_macro_provider import CZECH_SERIES_DEFINITIONS, fetch_czech_macro_dashboard
from data_provider import load_company_snapshot
from fred_provider import FRED_SERIES_DEFINITIONS, fetch_macro_dashboard
from sec_insider_provider import load_insider_transactions


def load_price_history(ticker_symbol: str, period: str = "5y") -> tuple[pd.DataFrame, list[str]]:
    import yfinance as yf

    ticker = yf.Ticker(ticker_symbol.upper())
    warnings: list[str] = []

    try:
        history = ticker.history(period=period, auto_adjust=False)
    except Exception as exc:
        return pd.DataFrame(), [f"Nepodarilo se nacist cenovou historii pro {ticker_symbol.upper()}: {exc}"]

    if history.empty:
        warnings.append(
            f"Cenova historie pro {ticker_symbol.upper()} za obdobi {period} neni v Yahoo Finance dostupna."
        )
        return pd.DataFrame(), warnings

    if "Close" not in history.columns:
        warnings.append(
            f"Yahoo Finance nevratil sloupec Close pro {ticker_symbol.upper()} za obdobi {period}."
        )
        return pd.DataFrame(), warnings

    close_history = history[["Close"]].copy()
    close_history.index = pd.to_datetime(close_history.index)
    close_history.rename(columns={"Close": "Close Price"}, inplace=True)
    return close_history, warnings


CRYPTO_DATA_SOURCE_VERSION = "btc_history_yfinance_whales_v2"
FRED_DATA_SOURCE_VERSION = "fred_series_v2_" + "_".join(
    definition.series_id for definition in FRED_SERIES_DEFINITIONS
)
CZECH_MACRO_DATA_SOURCE_VERSION = "czech_macro_v1_" + "_".join(
    definition.key for definition in CZECH_SERIES_DEFINITIONS
)
APP_VERSION = "1.4.3"


st.set_page_config(page_title="Buffett Analyzer", layout="wide")

st.markdown(
    """
    <style>
    .block-container {
        padding-top: 1.2rem;
        padding-bottom: 1rem;
        max-width: 98vw;
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
    .app-version {
        font-size: 0.78rem;
        color: #6b7280;
        letter-spacing: 0.03em;
        margin-bottom: 0.15rem;
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


def format_percent_points(value: float | str | None) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, str):
        return value
    return f"{value:.2f} %"


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


def insider_transactions_dataframe(frame: pd.DataFrame, currency: str | None) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()

    def format_insider_price(value: float | None) -> str:
        if value is None or pd.isna(value):
            return "N/A"
        suffix = f" {currency}" if currency else ""
        return f"{value:,.2f}{suffix}"

    display_frame = frame.copy()
    display_frame["Datum transakce"] = display_frame["transaction_date"].dt.strftime("%d.%m.%Y")
    display_frame["Datum podani"] = display_frame["filing_date"].dt.strftime("%d.%m.%Y")
    display_frame["Vykazujici osoba"] = display_frame["reporting_owner"]
    display_frame["Vztah"] = display_frame["relationship"]
    display_frame["Typ pohybu"] = display_frame["transaction_kind"]
    display_frame["Kod"] = display_frame["transaction_code"]
    display_frame["Kod detail"] = display_frame["transaction_code_description"]
    display_frame["Akcii"] = display_frame["shares"].map(lambda value: f"{value:,.0f}")
    display_frame["Cena za akcii"] = display_frame["price_per_share"].map(format_insider_price)
    display_frame["Po transakci"] = display_frame["shares_following"].map(
        lambda value: "N/A" if value is None or pd.isna(value) else f"{value:,.0f}"
    )
    display_frame["Form"] = display_frame["form_type"]
    display_frame["10b5-1"] = display_frame["aff_10b5_one"].map(lambda value: "Ano" if value else "Ne")
    return display_frame[
        [
            "Datum transakce",
            "Datum podani",
            "Vykazujici osoba",
            "Vztah",
            "Typ pohybu",
            "Kod",
            "Kod detail",
            "Akcii",
            "Cena za akcii",
            "Po transakci",
            "Form",
            "10b5-1",
        ]
    ]


def classify_insider_group(relationship: str) -> str:
    relationship_text = str(relationship or "")
    if "Officer" in relationship_text or "Director" in relationship_text:
        return "Aktualni vedeni"
    if "10% Owner" in relationship_text:
        return "10% vlastnik"
    if relationship_text.strip() and relationship_text.strip() != "Neuvedeno":
        return "Jina hlasici osoba"
    return "Neuvedeno"


def metric_caption(title: str, body: str) -> None:
    st.markdown(f"**{title}**  \n{body}")


def load_fred_api_key() -> str:
    config_path = Path(__file__).with_name("config.json")
    if config_path.exists():
        try:
            with config_path.open("r", encoding="utf-8") as config_file:
                config = json.load(config_file)
            api_key = str(config.get("fred_api_key", "")).strip()
            if api_key:
                return api_key
        except (OSError, json.JSONDecodeError):
            pass

    return str(st.secrets.get("FRED_API_KEY", "") or os.getenv("FRED_API_KEY", "")).strip()


def ensure_session_state() -> None:
    st.session_state.setdefault("us_single_analysis", st.session_state.get("single_analysis"))
    st.session_state.setdefault("us_single_ticker", st.session_state.get("single_ticker", ""))
    st.session_state.setdefault("cz_single_analysis", None)
    st.session_state.setdefault("cz_single_ticker", "")
    st.session_state.setdefault("us_active_buffett_section", "Analyza")
    st.session_state.setdefault("cz_active_buffett_section", "Analyza")
    st.session_state.setdefault("macro_last_completed_at", "")
    st.session_state.setdefault("crisis_last_completed_at", "")
    st.session_state.setdefault("crypto_last_completed_at", "")
    st.session_state.setdefault("czech_macro_last_completed_at", "")


@st.cache_resource
def get_batch_state(scope: str = "us") -> dict:
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
        "source_version": FRED_DATA_SOURCE_VERSION,
    }


@st.cache_resource
def get_czech_macro_state() -> dict:
    return {
        "lock": Lock(),
        "running": False,
        "results": [],
        "errors": [],
        "status": "",
        "updated_at": "",
        "done": 0,
        "total": 0,
        "started_at": "",
        "source_version": CZECH_MACRO_DATA_SOURCE_VERSION,
    }


@st.cache_resource
def get_crypto_state() -> dict:
    return {
        "lock": Lock(),
        "running": False,
        "dashboard": None,
        "days": 365,
        "status": "",
        "updated_at": "",
        "error": None,
        "started_at": "",
        "source_version": CRYPTO_DATA_SOURCE_VERSION,
    }


def build_batch_row(analysis) -> dict[str, str]:
    company = analysis.company
    current_price = getattr(company, "current_price", None)
    intrinsic_value = getattr(company, "intrinsic_value_per_share", None)
    buy_under_price = getattr(company, "buy_under_price", None)
    trailing_pe = getattr(company, "trailing_pe", None)
    last_year_dividend_yield = getattr(company, "last_year_dividend_yield", None)
    dividend_yield_5y = getattr(company, "five_year_avg_dividend_yield", None)

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
        "Sektor": company.sector or "N/A",
        "Odvetvi": company.industry or "N/A",
        "Cena": format_value(current_price, "currency_decimal", company.currency),
        "P/E": format_value(trailing_pe),
        "Div. 5Y": format_percent_points(dividend_yield_5y),
        "Div. 1Y": format_percent_points(last_year_dividend_yield),
        "Vnitrni hodn.": format_value(intrinsic_value, "currency_decimal", company.currency),
        "Nakupni cena": format_value(buy_under_price, "currency_decimal", company.currency),
        "Score": score_text,
        "Signal": signal,
        "Valuace": valuation,
        "Rozdil": price_gap,
        "Varovani": str(len(analysis.warnings)),
    }


def style_batch_results(frame: pd.DataFrame):
    def row_style(row):
        if row.get("Valuace") == "Pod nakupni cenou":
            return ["background-color: #eef9f0; color: #000000"] * len(row)
        return [""] * len(row)

    return frame.style.apply(row_style, axis=1)


def batch_column_config() -> dict:
    return {
        "Ticker": st.column_config.TextColumn("Ticker", width="small"),
        "Firma": st.column_config.TextColumn("Firma", width="medium"),
        "Sektor": st.column_config.TextColumn("Sektor", width="medium"),
        "Odvetvi": st.column_config.TextColumn("Odvetvi", width="medium"),
        "Cena": st.column_config.TextColumn("Cena", width="small"),
        "P/E": st.column_config.TextColumn("P/E", width="small"),
        "Div. 5Y": st.column_config.TextColumn("Div. 5Y", width="small"),
        "Div. 1Y": st.column_config.TextColumn("Div. 1Y", width="small"),
        "Vnitrni hodn.": st.column_config.TextColumn("Vnitrni hodn.", width="small"),
        "Nakupni cena": st.column_config.TextColumn("Nakupni cena", width="small"),
        "Score": st.column_config.TextColumn("Score", width="small"),
        "Signal": st.column_config.TextColumn("Signal", width="medium"),
        "Valuace": st.column_config.TextColumn("Valuace", width="medium"),
        "Rozdil": st.column_config.TextColumn("Rozdil", width="small"),
        "Varovani": st.column_config.TextColumn("Varovani", width="small"),
    }


def batch_column_order(show_last_year_dividend: bool = False) -> list[str]:
    columns = [
        "Ticker",
        "Firma",
        "Sektor",
        "Odvetvi",
        "Cena",
        "P/E",
        "Div. 5Y",
        "Div. 1Y",
        "Vnitrni hodn.",
        "Nakupni cena",
        "Score",
        "Signal",
        "Valuace",
        "Rozdil",
        "Varovani",
    ]
    if not show_last_year_dividend:
        columns.remove("Div. 1Y")
    return columns


def filter_options(frame: pd.DataFrame, column: str) -> list[str]:
    if column not in frame:
        return []
    values = [value for value in frame[column].dropna().unique().tolist() if value != "N/A"]
    return sorted(str(value) for value in values)


def build_failed_batch_row(company, error: Exception) -> dict[str, str]:
    return {
        "Ticker": company.ticker,
        "Firma": company.name,
        "Sektor": "N/A",
        "Odvetvi": "N/A",
        "Cena": "N/A",
        "P/E": "N/A",
        "Div. 5Y": "N/A",
        "Div. 1Y": "N/A",
        "Vnitrni hodn.": "N/A",
        "Nakupni cena": "N/A",
        "Score": "N/A",
        "Signal": "Chyba analyzy",
        "Valuace": "N/A",
        "Rozdil": "N/A",
        "Varovani": f"1: {error}",
    }


def start_batch_analysis(companies, scope: str = "us") -> bool:
    state = get_batch_state(scope)
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
                    snapshot = load_company_snapshot(company.ticker, use_sec_statements=(scope == "us"))
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
        "do nekolika srozumitelnych bodu nad dostupnymi trznimi a ucetnimi daty."
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
        "aktualni `Free Cash Flow` z dostupnych ucetnich vykazu. Nejde o presnou predpoved budoucnosti, ale o "
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


@st.cache_data(show_spinner=False)
def get_price_history_chart_data(ticker: str, period: str) -> tuple[pd.DataFrame, list[str]]:
    return load_price_history(ticker, period)


@st.cache_data(show_spinner="Nacitam insider transakce ze SEC...")
def get_insider_transactions_data(
    ticker: str,
    years: int = 5,
    cik: str | None = None,
    management_only: bool = True,
) -> tuple[pd.DataFrame, list[str]]:
    return load_insider_transactions(ticker, years, cik_override=cik, management_only=management_only)


@st.cache_resource
def get_insider_state() -> dict:
    return {
        "lock": Lock(),
        "records": {},
    }


def insider_state_key(ticker: str, years: int, cik: str | None, management_only: bool) -> str:
    return f"{ticker.upper()}::{years}::{(cik or '').strip()}::{management_only}"


def read_insider_state(ticker: str, years: int, cik: str | None, management_only: bool) -> dict:
    state = get_insider_state()
    record_key = insider_state_key(ticker, years, cik, management_only)
    with state["lock"]:
        record = state["records"].get(
            record_key,
            {
                "running": False,
                "frame": None,
                "warnings": [],
                "error": None,
                "updated_at": "",
            },
        )
        return {
            "running": record["running"],
            "frame": record["frame"],
            "warnings": list(record["warnings"]),
            "error": record["error"],
            "updated_at": record["updated_at"],
        }


def start_insider_transactions_load(ticker: str, years: int, cik: str | None, management_only: bool) -> bool:
    state = get_insider_state()
    record_key = insider_state_key(ticker, years, cik, management_only)
    with state["lock"]:
        record = state["records"].get(record_key)
        if record and record.get("running"):
            return False
        state["records"][record_key] = {
            "running": True,
            "frame": None,
            "warnings": [],
            "error": None,
            "updated_at": "",
        }

    def worker() -> None:
        try:
            frame, warnings = load_insider_transactions(
                ticker,
                years,
                cik_override=cik,
                management_only=management_only,
            )
            with state["lock"]:
                state["records"][record_key] = {
                    "running": False,
                    "frame": frame,
                    "warnings": warnings,
                    "error": None,
                    "updated_at": pd.Timestamp.now().strftime("%d.%m.%Y %H:%M"),
                }
        except Exception as exc:
            with state["lock"]:
                state["records"][record_key] = {
                    "running": False,
                    "frame": pd.DataFrame(),
                    "warnings": [],
                    "error": str(exc),
                    "updated_at": "",
                }

    Thread(target=worker, daemon=True).start()
    return True


def render_insider_transactions_tab(company, scope: str) -> None:
    if scope != "us":
        st.info("SEC insider transakce jsou v teto verzi dostupne jen pro americke firmy se SEC Form 3/4/5.")
        return

    st.markdown("**Nakupy a prodeje akcii vedenim a dalsimi hlasicimi osobami**")
    st.caption(
        "Zdroj: SEC EDGAR Form 3/4/5. Tabulka a graf pracuji s ne-derivativnimi transakcemi hlasenymi insiderem."
    )
    st.caption(
        "Zobrazeni je nastaveno na poslednich 5 let a jen na aktualni vedeni, tedy osoby s roli `Officer` nebo `Director` v SEC filingach."
    )
    cik = company.raw_info.get("sec_cik") if isinstance(company.raw_info, dict) else None
    state = read_insider_state(company.ticker, 5, cik, True)
    if state["frame"] is None and not state["running"] and not state["error"]:
        start_insider_transactions_load(company.ticker, 5, cik, True)
        state = read_insider_state(company.ticker, 5, cik, True)

    if state["running"]:
        st.info("Nacitam insider transakce ze SEC. Tahle sekce se po dokonceni sama obnovi.")
        time.sleep(2)
        st.rerun()
        return

    if state["error"]:
        st.warning(state["error"])
        return

    insider_frame = state["frame"] if isinstance(state["frame"], pd.DataFrame) else pd.DataFrame()
    insider_warnings = state["warnings"]
    for warning in insider_warnings:
        st.warning(warning)

    if insider_frame.empty:
        st.info("Za poslednich 5 let nejsou pro tuto firmu dostupne insider transakce z SEC.")
        return

    if state["updated_at"]:
        st.caption(f"Posledni nacteni insider dat: {state['updated_at']}")

    filtered_frame = insider_frame.copy()
    filtered_frame["insider_group"] = filtered_frame["relationship"].map(classify_insider_group)

    filter1, filter2, filter3 = st.columns([1.15, 1.45, 1.15])
    with filter1:
        group_mode = st.selectbox(
            "Typ vedeni",
            options=[
                "Vsechno vedeni",
                "Jen officeri",
                "Jen directori",
            ],
            key=f"{scope}_insider_group_mode_{company.ticker}",
        )
    with filter2:
        owner_options = sorted(
            owner for owner in filtered_frame["reporting_owner"].dropna().astype(str).unique().tolist() if owner
        )
        selected_owners = st.multiselect(
            "Filtr podle osoby",
            options=owner_options,
            placeholder="Vsechny osoby",
            key=f"{scope}_insider_owner_filter_{company.ticker}",
        )
    with filter3:
        movement_options = sorted(
            movement for movement in filtered_frame["transaction_kind"].dropna().astype(str).unique().tolist() if movement
        )
        selected_movements = st.multiselect(
            "Typ pohybu",
            options=movement_options,
            placeholder="Vsechny pohyby",
            key=f"{scope}_insider_movement_filter_{company.ticker}",
        )

    filtered_frame = filtered_frame[filtered_frame["insider_group"] == "Aktualni vedeni"]
    if group_mode == "Jen officeri":
        filtered_frame = filtered_frame[
            filtered_frame["relationship"].fillna("").str.contains("Officer", case=False, na=False)
        ]
    elif group_mode == "Jen directori":
        filtered_frame = filtered_frame[
            filtered_frame["relationship"].fillna("").str.contains("Director", case=False, na=False)
        ]

    if selected_owners:
        filtered_frame = filtered_frame[filtered_frame["reporting_owner"].isin(selected_owners)]
    if selected_movements:
        filtered_frame = filtered_frame[filtered_frame["transaction_kind"].isin(selected_movements)]

    st.caption(
        "Tohle je rychlejsi rezim: tab zobrazuje jen hlasene transakce aktualniho vedeni, ne vsech insider osob."
    )

    if filtered_frame.empty:
        st.warning("Pro zvolene filtry nejsou dostupne zadne insider transakce vedeni.")
        return

    acquisitions = filtered_frame.loc[filtered_frame["signed_shares"] > 0, "signed_shares"].sum()
    dispositions = filtered_frame.loc[filtered_frame["signed_shares"] < 0, "signed_shares"].abs().sum()
    filings_count = int(filtered_frame[["filing_date", "reporting_owner"]].drop_duplicates().shape[0])
    transactions_count = int(len(filtered_frame))

    sum1, sum2, sum3, sum4 = st.columns(4)
    sum1.metric("Hlasene transakce", f"{transactions_count:,}".replace(",", " "))
    sum2.metric("Unikatni podani", f"{filings_count:,}".replace(",", " "))
    sum3.metric("Nakoupene akcie", f"{acquisitions:,.0f}".replace(",", " "))
    sum4.metric("Prodane akcie", f"{dispositions:,.0f}".replace(",", " "))

    monthly_frame = filtered_frame.copy()
    monthly_frame["Mesic"] = monthly_frame["transaction_date"].dt.to_period("M").dt.to_timestamp()
    monthly_chart = (
        monthly_frame.groupby("Mesic", as_index=True)
        .agg(
            Nakoupeno=("signed_shares", lambda values: sum(value for value in values if value > 0)),
            Prodano=("signed_shares", lambda values: abs(sum(value for value in values if value < 0))),
            Cisty_pohyb=("signed_shares", "sum"),
        )
        .sort_index()
    )

    chart_col, note_col = st.columns([3.2, 1.3])
    with chart_col:
        st.markdown("**Vyvoj insider aktivit za poslednich 5 let**")
        st.bar_chart(monthly_chart[["Nakoupeno", "Prodano"]], use_container_width=True, height=320)
    with note_col:
        metric_caption("Nakoupeno", "Souhrn hlasenych nabyti akcii za dany mesic.")
        metric_caption("Prodano", "Souhrn hlasenych prodeju nebo snizeni pozice za dany mesic.")
        metric_caption("SEC Form 4/5", "Jde o hlasene insider transakce osob, ktere je museji SEC oznamovat.")
        metric_caption("Poznamka", "Data mohou obsahovat granty, exercise a dane. Proto je dulezite cist i transaction code.")

    with st.expander("Zobrazit cisty mesicni pohyb", expanded=False):
        st.line_chart(monthly_chart[["Cisty_pohyb"]], use_container_width=True, height=220)
        st.caption("Kladna hodnota znamena prevahu hlasenych nakupu, zaporna prevahu prodeju nebo snizeni pozice.")

    st.dataframe(
        insider_transactions_dataframe(filtered_frame, company.currency),
        use_container_width=True,
        hide_index=True,
        height=620,
    )


def render_single_analysis(analysis, scope: str) -> None:
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
    if company.source_notes:
        st.caption(" | ".join(company.source_notes))

    if analysis.warnings:
        with st.expander(f"Varovani a chybejici data ({len(analysis.warnings)})", expanded=False):
            for warning in analysis.warnings:
                st.warning(warning)

    detail_view = st.radio(
        "Detail firmy",
        options=["Prehled", "Metriky", "Insider transakce"],
        horizontal=True,
        label_visibility="collapsed",
        key=f"{scope}_single_detail_view",
    )

    if detail_view == "Prehled":
        period_options = {
            "1 rok": "1y",
            "3 roky": "3y",
            "5 let": "5y",
            "10 let": "10y",
            "Max": "max",
        }
        chart_col, control_col = st.columns([3.3, 1.2])
        with control_col:
            selected_period_label = st.selectbox(
                "Obdobi grafu",
                options=list(period_options.keys()),
                index=2,
                key=f"{scope}_price_history_period",
                help="Meni historicke obdobi cenoveho grafu v zalozce Prehled.",
            )
        selected_period = period_options[selected_period_label]
        price_history, history_warnings = get_price_history_chart_data(company.ticker, selected_period)
        with chart_col:
            st.markdown("**Vyvoj ceny akcie**")
            if price_history.empty:
                st.warning("Cenovy graf neni pro zvolene obdobi dostupny.")
            else:
                st.line_chart(price_history, y="Close Price", use_container_width=True, height=320)
                st.caption(
                    f"Zdroj: Yahoo Finance pres yfinance. Zobrazen je sloupec Close pro ticker {company.ticker}."
                )
        for warning in history_warnings:
            st.warning(warning)

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

    if detail_view == "Metriky":
        st.dataframe(
            metrics_dataframe(analysis.metrics, company.currency),
            use_container_width=True,
            hide_index=True,
            height=620,
        )

    if detail_view == "Insider transakce":
        render_insider_transactions_tab(company, scope)


def read_batch_state(scope: str = "us") -> dict:
    state = get_batch_state(scope)
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


def render_batch_analysis(scope: str = "us") -> None:
    state = read_batch_state(scope)
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
        st.info("Zatim tu neni hromadna analyza. V horni casti teto zalozky klikni na `Hromadna analyza`.")
        if state["running"]:
            time.sleep(2)
            st.rerun()
        return

    results_frame = pd.DataFrame(results)
    if "Div. 1Y" not in results_frame:
        results_frame["Div. 1Y"] = "N/A"

    filter1, filter2, filter3, filter4 = st.columns([1, 1, 1, 0.9])
    with filter1:
        selected_sectors = st.multiselect(
            "Filtr podle sektoru",
            options=filter_options(results_frame, "Sektor"),
            placeholder="Vsechny sektory",
            key=f"{scope}_sector_filter",
        )
    with filter2:
        selected_industries = st.multiselect(
            "Filtr podle odvetvi",
            options=filter_options(results_frame, "Odvetvi"),
            placeholder="Vsechna odvetvi",
            key=f"{scope}_industry_filter",
        )
    with filter3:
        selected_signals = st.multiselect(
            "Filtr podle signalu",
            options=filter_options(results_frame, "Signal"),
            placeholder="Vsechny signaly",
            key=f"{scope}_signal_filter",
        )
    with filter4:
        show_last_year_dividend = st.checkbox(
            "Zobrazit Div. 1Y",
            value=False,
            help="Zobrazi dividendovy vynos za poslednich 12 mesicu. Sloupec je defaultne skryty.",
            key=f"{scope}_show_last_year_dividend",
        )

    filtered_frame = results_frame
    if selected_sectors:
        filtered_frame = filtered_frame[filtered_frame["Sektor"].isin(selected_sectors)]
    if selected_industries:
        filtered_frame = filtered_frame[filtered_frame["Odvetvi"].isin(selected_industries)]
    if selected_signals:
        filtered_frame = filtered_frame[filtered_frame["Signal"].isin(selected_signals)]

    st.caption(f"Zobrazeno {len(filtered_frame)} z {len(results_frame)} firem.")
    if filtered_frame.empty:
        st.warning("Pro zvolene filtry nejsou dostupne zadne firmy.")
        return

    st.dataframe(
        style_batch_results(filtered_frame),
        use_container_width=True,
        hide_index=True,
        height=900,
        column_order=batch_column_order(show_last_year_dividend),
        column_config=batch_column_config(),
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


CRISIS_SERIES_ORDER = [
    "FEDFUNDS",
    "CPIAUCSL",
    "UNRATE",
    "T10Y2Y",
    "T10Y3M",
    "VIXCLS",
    "BAA10Y",
    "M2SL",
    "GDP",
    "SAHMREALTIME",
]


CRISIS_SERIES_EXPLANATIONS = {
    "FEDFUNDS": {
        "label": "Sazba Fedu",
        "chart": "Graf ukazuje efektivni sazbu Fed Funds, tedy kratkodobou sazbu, kterou Fed primo ovlivnuje.",
        "warning": "Kdyz sazba prudce roste nebo zustava vysoko, financovani firem i domacnosti zdrazuje a ekonomika se muze zacit brzdit. Prudke snizovani sazeb naopak casto prichazi az ve chvili, kdy Fed reaguje na zhorsujici se ekonomiku nebo financni stres.",
    },
    "CPIAUCSL": {
        "label": "Inflace USA z CPI",
        "chart": "FRED serie je cenovy index CPI. V teto zalozce ho prevadim na mezirocni zmenu v procentech, aby bylo videt skutecne inflacni tempo.",
        "warning": "Vysoka inflace muze nutit Fed drzet sazby vysoko, coz zvysuje riziko zpomaleni. Rychly pad inflace muze byt pozitivni, ale pokud prichazi soucasne s rostouci nezamestnanosti a slabym HDP, muze ukazovat ochlazeni poptavky.",
    },
    "UNRATE": {
        "label": "Nezamestnanost",
        "chart": "Graf ukazuje miru nezamestnanosti U-3 v USA.",
        "warning": "Samotna nizka nezamestnanost krizi nehlasi. Dulezity je hlavne rychly rust z nizkych hodnot. Kdyz nezamestnanost zacne zrychlovat, casto uz ekonomika slabne a firemni zisky se dostavaji pod tlak.",
    },
    "T10Y2Y": {
        "label": "Vynosova krivka 10Y minus 2Y",
        "chart": "Graf ukazuje rozdil mezi 10letym a 2letym vynosem americkych statnich dluhopisu.",
        "warning": "Hodnota pod nulou znamena inverzni vynosovou krivku. Historicky to byl jeden z nejsledovanejsich predstihovych signalu recese, ale nacasovani muze byt nepresne a recese muze prijit az se zpozdenim.",
    },
    "T10Y3M": {
        "label": "Vynosova krivka 10Y minus 3M",
        "chart": "Graf ukazuje rozdil mezi 10letym a 3mesicnim vynosem americkych statnich dluhopisu.",
        "warning": "Inverze pod nulou rika, ze kratke sazby jsou vysoko proti dlouhym vynosum. Trh tim casto signalizuje ocekavane zpomaleni, budouci pokles sazeb nebo recesni riziko.",
    },
    "VIXCLS": {
        "label": "VIX index",
        "chart": "Graf ukazuje ocekavanou volatilitu indexu S&P 500.",
        "warning": "VIX je spis teplomer strachu nez dlouhodoby predstihovy indikator. Hodnoty nad 30 casto znamenaji stres na trzich, hodnoty nad 40 paniku. Vysoky VIX muze ukazovat uz probihajici trzni krizi.",
    },
    "BAA10Y": {
        "label": "Kreditni spread BAA minus 10Y",
        "chart": "Graf ukazuje rozdil mezi firemnimi dluhopisy ratingu BAA a 10letym statnim dluhopisem USA.",
        "warning": "Kdyz spread roste, investori chteji vyssi odmenu za kreditni riziko. Prudke rozsireni spreadu casto znamena, ze se zhorsuje dostupnost kapitalu a trh se boji defaultu.",
    },
    "M2SL": {
        "label": "Penezni zasoba M2",
        "chart": "Graf ukazuje sirsi penezni zasobu M2. V popisu doplnuji i mezirocni zmenu, protoze ta lepe ukazuje zmenu likvidity.",
        "warning": "Slaby rust nebo pokles M2 muze znamenat utahovani likvidity. Samo o sobe to neni recesni signal, ale v kombinaci s vysokymi sazbami, inverzi krivky a rostouci nezamestnanosti je to varovne.",
    },
    "GDP": {
        "label": "HDP USA",
        "chart": "Graf ukazuje nominalni HDP USA. Pro krizovy pohled je dulezite sledovat hlavne zpomaleni a poklesy ekonomicke aktivity.",
        "warning": "Kdyz HDP klesa nebo prudce zpomaluje, krize uz muze probihat. HDP je obvykle zpozdeny ukazatel, proto je vhodne ho cist spolu s krivkou, nezamestnanosti a kreditnim spreadem.",
    },
    "SAHMREALTIME": {
        "label": "Sahm Rule recession indicator",
        "chart": "Graf ukazuje Sahm Rule indikator zalozeny na zhorseni nezamestnanosti.",
        "warning": "Hodnota kolem 0.5 a vyse je silny signal, ze ekonomika uz pravdepodobne vstoupila do recese. Je to spis potvrzovaci signal nez dlouhy predstihovy indikator.",
    },
}


def percent_change(series_result, periods: int) -> float | None:
    values = series_result.observations["value"].dropna()
    if len(values) <= periods:
        return None
    previous = values.iloc[-periods - 1]
    latest = values.iloc[-1]
    if previous == 0:
        return None
    return float((latest / previous - 1) * 100)


def crisis_chart_frame(series_result) -> tuple[pd.DataFrame, str]:
    series_id = series_result.definition.series_id
    values = series_result.observations["value"].dropna()

    if series_id == "CPIAUCSL":
        frame = pd.DataFrame({"Mezirocni inflace z CPI (%)": values.pct_change(12) * 100}).dropna()
        return frame, "CPI index prevedeny na mezirocni inflaci."

    if series_id == "M2SL":
        frame = pd.DataFrame({"M2 mezirocni zmena (%)": values.pct_change(12) * 100}).dropna()
        return frame, "M2 prevedena na mezirocni zmenu likvidity."

    if series_id == "GDP":
        frame = pd.DataFrame({"HDP mezirocni zmena (%)": values.pct_change(4) * 100}).dropna()
        return frame, "Nominalni HDP prevedeny na mezirocni zmenu."

    return series_result.observations.rename(columns={"value": series_result.definition.title}), "Surova FRED serie."


def latest_chart_value(frame: pd.DataFrame) -> float | None:
    if frame.empty:
        return None
    values = frame.iloc[:, 0].dropna()
    if values.empty:
        return None
    return float(values.iloc[-1])


def crisis_signal(series_result, chart_value: float | None) -> str:
    series_id = series_result.definition.series_id
    latest = series_result.latest_value

    if series_id in {"CPIAUCSL", "M2SL", "GDP"}:
        latest = chart_value

    if latest is None:
        return "N/A"

    if series_id == "FEDFUNDS":
        six_period_change = None
        values = series_result.observations["value"].dropna()
        if len(values) > 6:
            six_period_change = float(values.iloc[-1] - values.iloc[-7])
        if latest >= 5:
            return "Restriktivni sazby"
        if six_period_change is not None and six_period_change <= -0.75:
            return "Fed vyrazne uvolnuje"
        return "Neutralni signal"

    if series_id == "CPIAUCSL":
        if latest >= 5:
            return "Vysoka inflace"
        if latest >= 3:
            return "Zvysena inflace"
        if latest < 0:
            return "Deflacni tlak"
        return "Mirnejsi inflace"

    if series_id == "UNRATE":
        three_month_change = None
        values = series_result.observations["value"].dropna()
        if len(values) > 3:
            three_month_change = float(values.iloc[-1] - values.iloc[-4])
        if three_month_change is not None and three_month_change >= 0.5:
            return "Nezamestnanost rychle roste"
        if latest >= 6:
            return "Slaby pracovni trh"
        return "Pracovni trh zatim drzi"

    if series_id in {"T10Y2Y", "T10Y3M"}:
        if latest < 0:
            return "Inverze krivky"
        if latest < 0.5:
            return "Krivka je zplostela"
        return "Krivka neni v inverzi"

    if series_id == "VIXCLS":
        if latest >= 40:
            return "Trzni panika"
        if latest >= 30:
            return "Vysoky stres"
        if latest >= 20:
            return "Zvysena nervozita"
        return "Klidnejsi trh"

    if series_id == "BAA10Y":
        if latest >= 3:
            return "Vysoky kreditni stres"
        if latest >= 2:
            return "Zvysene kreditni riziko"
        return "Kreditni trh klidnejsi"

    if series_id == "M2SL":
        if latest < 0:
            return "M2 mezirocne klesa"
        if latest < 2:
            return "Slaby rust likvidity"
        if latest > 8:
            return "Silny rust likvidity"
        return "Stredni rust likvidity"

    if series_id == "GDP":
        if latest < 0:
            return "HDP mezirocne klesa"
        if latest < 2:
            return "Slabe tempo HDP"
        return "HDP stale roste"

    if series_id == "SAHMREALTIME":
        if latest >= 0.5:
            return "Recesni signal aktivni"
        if latest >= 0.3:
            return "Blizi se recesni hranici"
        return "Signal zatim neaktivni"

    return "N/A"


def render_crisis_series_card(series_result) -> None:
    series_id = series_result.definition.series_id
    explanation = CRISIS_SERIES_EXPLANATIONS.get(series_id, {})
    chart_frame, chart_note = crisis_chart_frame(series_result)
    chart_value = latest_chart_value(chart_frame)
    latest_date = (
        series_result.latest_date.strftime("%d.%m.%Y")
        if series_result.latest_date is not None
        else "N/A"
    )
    primary_value = chart_value if series_id in {"CPIAUCSL", "M2SL", "GDP"} else series_result.latest_value
    primary_units = "Percent" if series_id in {"CPIAUCSL", "M2SL", "GDP"} else series_result.units
    one_year_change = percent_change(series_result, 12)

    st.markdown(f"### {series_id} - {explanation.get('label', series_result.definition.title)}")
    metric1, metric2, metric3 = st.columns([1, 1, 1.2])
    metric1.metric("Posledni hodnota", format_macro_value(primary_value, primary_units))
    metric2.metric("Datum", latest_date)
    metric3.metric("Krizovy signal", crisis_signal(series_result, chart_value))

    if not chart_frame.empty:
        st.line_chart(chart_frame, use_container_width=True, height=260)
    else:
        st.info("Pro tuto serii zatim neni dost dat na krizovy graf.")

    st.write(explanation.get("chart", series_result.definition.description))
    st.write(explanation.get("warning", "Cti spolu s ostatnimi ukazateli, ne izolovane."))
    if one_year_change is not None and series_id not in {"CPIAUCSL", "M2SL", "GDP"}:
        st.caption(f"Mezirocni zmena surove serie: {one_year_change:.2f} %")
    st.caption(
        f"Serie: {series_id} | Jednotky FRED: {series_result.units} | Frekvence: {series_result.frequency} | {chart_note}"
    )


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
    st.write(series_result.definition.description)
    st.caption(
        f"Serie: {series_result.definition.series_id} | Jednotky: {series_result.units} | Frekvence: {series_result.frequency}"
    )


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
        state["total"] = len(FRED_SERIES_DEFINITIONS)
        state["started_at"] = pd.Timestamp.now().strftime("%d.%m.%Y %H:%M:%S")
        state["source_version"] = FRED_DATA_SOURCE_VERSION

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
    api_key = load_fred_api_key()
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
        if state.get("source_version") != FRED_DATA_SOURCE_VERSION:
            state["running"] = False
            state["results"] = []
            state["errors"] = []
            state["status"] = ""
            state["updated_at"] = ""
            state["api_key"] = ""
            state["done"] = 0
            state["total"] = 0
            state["started_at"] = ""
            state["source_version"] = FRED_DATA_SOURCE_VERSION

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
            "source_version": state["source_version"],
        }


def render_macro_sections(results) -> None:
    categories = [
        "Menova politika a inflace",
        "Trzni stres a krize",
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
        "Zdroj dat: FRED (Federal Reserve Economic Data). Data se nacitaji na pozadi."
    )


def render_macro_toolbar(
    state: dict,
    api_key: str,
    auto_rerun_on_start: bool = False,
    button_label: str = "Obnovit makro data",
) -> dict:
    header1, header2 = st.columns([1, 3])
    with header1:
        if st.button(button_label, use_container_width=True):
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
    api_key = load_fred_api_key()
    if not api_key:
        st.info(
            "Pro nacitani makro dat chybi `fred_api_key` v `config.json`, Streamlit secrets nebo `FRED_API_KEY`."
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


def render_crisis_header() -> None:
    st.subheader("Krize - varovne makro a trzni indikatory")
    st.write(
        "Tato zalozka sleduje vybrane FRED serie, ktere pomahaji odhadnout, jestli se v USA zvysuje riziko krize, "
        "nebo jestli uz ekonomika a trhy vykazuji znamky stresu. Jeden ukazatel sam o sobe nestaci; dulezita je kombinace sazeb, inflace, nezamestnanosti, vynosove krivky, volatility, kreditu, likvidity a HDP."
    )
    st.caption("Zdroj dat: FRED. Nejde o investicni doporuceni ani predpoved jistoty recese.")


def render_crisis_sections(results, running: bool = False) -> None:
    result_map = {item.definition.series_id: item for item in results}
    available_count = sum(1 for series_id in CRISIS_SERIES_ORDER if series_id in result_map)
    st.caption(f"Dostupne krizove ukazatele: {available_count}/{len(CRISIS_SERIES_ORDER)}")

    for series_id in CRISIS_SERIES_ORDER:
        st.markdown("---")
        series_result = result_map.get(series_id)
        if series_result is None:
            explanation = CRISIS_SERIES_EXPLANATIONS.get(series_id, {})
            st.subheader(f"{series_id} - {explanation.get('label', 'N/A')}")
            if running:
                st.info("Tato FRED serie se jeste nacita. Graf se doplni automaticky, jakmile prijde odpoved z FRED.")
            else:
                st.warning("Tato FRED serie se nepodarila nacist. Zkus obnovit krizova data.")
            if explanation:
                st.write(explanation["warning"])
            continue
        render_crisis_series_card(series_result)


@st.fragment(run_every=1)
def render_crisis_loading_view(api_key: str) -> None:
    state = read_macro_state()
    state = render_macro_toolbar(state, api_key, button_label="Obnovit krizova data")

    if state["running"]:
        total = max(state["total"], 1)
        st.progress(state["done"] / total, text=state["status"])
        st.info("Krizove indikatory se nacitaji z FRED na pozadi. Hotove grafy se budou postupne doplnovat.")
        if state["errors"]:
            for error in state["errors"]:
                st.warning(error)
        if state["results"]:
            render_crisis_sections(state["results"], running=True)
        return

    if state["updated_at"] and st.session_state.crisis_last_completed_at != state["updated_at"]:
        st.session_state.crisis_last_completed_at = state["updated_at"]
        st.rerun()

    if state["errors"] and not state["results"]:
        for error in state["errors"]:
            st.warning(error)
        return

    if state["results"]:
        render_crisis_sections(state["results"], running=state["running"])


def render_crisis_analysis() -> None:
    render_crisis_header()
    api_key = load_fred_api_key()
    if not api_key:
        st.info(
            "Pro nacitani krizovych dat chybi `fred_api_key` v `config.json`, Streamlit secrets nebo `FRED_API_KEY`."
        )
        return

    state = read_macro_state()
    key_changed = state["api_key"] != api_key
    if key_changed or (not state["running"] and not state["results"] and not state["errors"]):
        start_macro_analysis(api_key)
        st.session_state.crisis_last_completed_at = ""
        state = read_macro_state()

    if state["running"]:
        render_crisis_loading_view(api_key)
        return

    state = render_macro_toolbar(
        state,
        api_key,
        auto_rerun_on_start=True,
        button_label="Obnovit krizova data",
    )

    if state["errors"] and not state["results"]:
        for error in state["errors"]:
            st.warning(error)
        return

    if state["errors"]:
        for error in state["errors"]:
            st.warning(error)

    if not state["results"]:
        st.warning("Nepodarilo se nacist zadna krizova data z FRED.")
        return

    render_crisis_sections(state["results"], running=False)


def start_czech_macro_analysis() -> bool:
    state = get_czech_macro_state()
    with state["lock"]:
        if state["running"]:
            return False
        state["running"] = True
        state["results"] = []
        state["errors"] = []
        state["status"] = "Nacitam ceska makro data..."
        state["updated_at"] = ""
        state["done"] = 0
        state["total"] = len(CZECH_SERIES_DEFINITIONS)
        state["started_at"] = pd.Timestamp.now().strftime("%d.%m.%Y %H:%M:%S")
        state["source_version"] = CZECH_MACRO_DATA_SOURCE_VERSION

    def worker() -> None:
        def on_progress(results, errors, done, total) -> None:
            with state["lock"]:
                state["results"] = results
                state["errors"] = errors
                state["done"] = done
                state["total"] = total
                state["status"] = f"Nacteno {done}/{total} ceskych ukazatelu..."

        try:
            results, errors = fetch_czech_macro_dashboard(progress_callback=on_progress)
            with state["lock"]:
                state["results"] = results
                state["errors"] = errors
                state["running"] = False
                state["done"] = len(CZECH_SERIES_DEFINITIONS)
                state["total"] = len(CZECH_SERIES_DEFINITIONS)
                state["status"] = "Ceska makro data nactena."
                state["updated_at"] = pd.Timestamp.now().strftime("%d.%m.%Y %H:%M")
        except Exception as exc:
            with state["lock"]:
                state["running"] = False
                state["errors"] = [str(exc)]
                state["status"] = "Ceska makro data se nepodarilo nacist."

    Thread(target=worker, daemon=True).start()
    return True


def read_czech_macro_state() -> dict:
    state = get_czech_macro_state()
    with state["lock"]:
        if state.get("source_version") != CZECH_MACRO_DATA_SOURCE_VERSION:
            state["running"] = False
            state["results"] = []
            state["errors"] = []
            state["status"] = ""
            state["updated_at"] = ""
            state["done"] = 0
            state["total"] = 0
            state["started_at"] = ""
            state["source_version"] = CZECH_MACRO_DATA_SOURCE_VERSION

        return {
            "running": state["running"],
            "results": list(state["results"]),
            "errors": list(state["errors"]),
            "status": state["status"],
            "updated_at": state["updated_at"],
            "done": state["done"],
            "total": state["total"],
            "started_at": state["started_at"],
            "source_version": state["source_version"],
        }


def czech_period_days(label: str) -> int | None:
    return {
        "1 rok": 365,
        "3 roky": 365 * 3,
        "5 let": 365 * 5,
        "10 let": 365 * 10,
        "Maximum": None,
    }.get(label)


def filter_czech_frame_by_period(frame: pd.DataFrame, period_label: str) -> pd.DataFrame:
    if frame.empty:
        return frame
    days = czech_period_days(period_label)
    if days is None:
        return frame
    cutoff = frame.index.max() - pd.Timedelta(days=days)
    filtered = frame[frame.index >= cutoff]
    return filtered if not filtered.empty else frame


def format_czech_macro_value(value: float | None, unit: str) -> str:
    if value is None:
        return "N/A"
    if unit == "%":
        return f"{value:.2f} %"
    if unit == "CZK":
        return f"{value:.2f} CZK"
    return f"{value:.2f}"


def render_czech_header() -> None:
    st.subheader("ČR - makroekonomicky prehled")
    st.write(
        "Tato zalozka sleduje inflaci, sazby, kurz koruny, HDP, nezamestnanost, mzdy, prumysl, maloobchod a stavebnictvi. "
        "Cilem je videt, jestli se v Cesku blizi inflacni tlak, zpomaleni ekonomiky nebo jina makro pohroma."
    )
    st.caption(
        "Zdroje dat: ČSÚ DataStat, ČNB a Eurostat. Ukazatele jsou informativni analyticky prehled, ne investicni doporuceni."
    )


def render_czech_toolbar(state: dict) -> tuple[dict, str]:
    column1, column2, column3 = st.columns([1.1, 1, 2.4])
    with column1:
        period_label = st.selectbox(
            "Obdobi grafu",
            ["1 rok", "3 roky", "5 let", "10 let", "Maximum"],
            index=2,
            key="czech_macro_period",
        )
    with column2:
        if st.button("Obnovit data ČR", use_container_width=True):
            start_czech_macro_analysis()
            state = read_czech_macro_state()
    with column3:
        if state["updated_at"]:
            st.caption(f"Posledni uspesne nacteni: {state['updated_at']}")
        elif state["started_at"]:
            st.caption(f"Spusteno: {state['started_at']}")
    return state, period_label


def render_czech_series_card(series_result, period_label: str) -> None:
    definition = series_result.definition
    frame = filter_czech_frame_by_period(series_result.observations, period_label)
    latest_date = (
        series_result.latest_date.strftime("%d.%m.%Y")
        if series_result.latest_date is not None
        else "N/A"
    )

    st.markdown(f"### {definition.title}")
    metric_columns = st.columns(min(3, len(series_result.observations.columns)) or 1)
    for column_container, column_name in zip(metric_columns, series_result.observations.columns[:3]):
        series = series_result.observations[column_name].dropna()
        latest_value = float(series.iloc[-1]) if not series.empty else None
        column_container.metric(column_name, format_czech_macro_value(latest_value, definition.unit))

    st.caption(f"Datum posledni hodnoty: {latest_date}")
    if not frame.empty:
        st.line_chart(frame, use_container_width=True, height=270)
    else:
        st.info("Pro vybrane obdobi nejsou dostupna data.")

    st.write(definition.description)
    st.write(definition.interpretation)
    st.caption(
        f"Zdroj: [{definition.source}]({definition.source_url}) | Jednotky: {definition.unit}"
    )


def render_czech_sections(results, period_label: str, running: bool = False) -> None:
    categories = ["Inflace", "Sazby a měna", "Reálná ekonomika", "Trh práce a mzdy"]
    result_map = {item.definition.key: item for item in results}
    st.caption(f"Dostupne ukazatele: {len(result_map)}/{len(CZECH_SERIES_DEFINITIONS)}")

    for category in categories:
        st.markdown("---")
        st.subheader(category)
        definitions = [item for item in CZECH_SERIES_DEFINITIONS if item.category == category]
        for index in range(0, len(definitions), 2):
            columns = st.columns(2)
            for column, definition in zip(columns, definitions[index : index + 2]):
                with column:
                    result = result_map.get(definition.key)
                    if result is None:
                        st.markdown(f"### {definition.title}")
                        if running:
                            st.info("Tento ukazatel se jeste nacita. Graf se doplni automaticky, jakmile prijde odpoved.")
                        else:
                            st.warning("Tento ukazatel se nepodarilo nacist nebo zatim nema dostupna data.")
                        st.write(definition.description)
                        st.caption(f"Zdroj: [{definition.source}]({definition.source_url})")
                        continue
                    render_czech_series_card(result, period_label)


@st.fragment(run_every=1)
def render_czech_loading_view() -> None:
    state = read_czech_macro_state()
    state, period_label = render_czech_toolbar(state)

    if state["running"]:
        total = max(state["total"], 1)
        st.progress(state["done"] / total, text=state["status"])
        st.info("Ceska makro data se nacitaji na pozadi. Hotove grafy se budou postupne doplnovat.")
        for error in state["errors"]:
            st.warning(error)
        if state["results"]:
            render_czech_sections(state["results"], period_label, running=True)
        return

    if (
        state["updated_at"]
        and st.session_state.czech_macro_last_completed_at != state["updated_at"]
    ):
        st.session_state.czech_macro_last_completed_at = state["updated_at"]
        st.rerun()

    for error in state["errors"]:
        st.warning(error)
    if state["results"]:
        render_czech_sections(state["results"], period_label, running=False)


def render_czech_republic_analysis() -> None:
    render_czech_header()
    state = read_czech_macro_state()

    if not state["running"] and not state["results"] and not state["errors"]:
        start_czech_macro_analysis()
        st.session_state.czech_macro_last_completed_at = ""
        state = read_czech_macro_state()

    if state["running"]:
        render_czech_loading_view()
        return

    state, period_label = render_czech_toolbar(state)

    if state["errors"] and not state["results"]:
        for error in state["errors"]:
            st.warning(error)
        return

    for error in state["errors"]:
        st.warning(error)

    if not state["results"]:
        st.warning("Nepodarilo se nacist zadna ceska makro data.")
        return

    render_czech_sections(state["results"], period_label, running=False)


def start_crypto_analysis(days: int | str = 365) -> bool:
    state = get_crypto_state()
    with state["lock"]:
        if state["running"]:
            return False
        state["running"] = True
        state["dashboard"] = None
        state["days"] = days
        state["status"] = "Nacitam bitcoinova data..."
        state["updated_at"] = ""
        state["error"] = None
        state["started_at"] = pd.Timestamp.now().strftime("%d.%m.%Y %H:%M:%S")
        state["source_version"] = CRYPTO_DATA_SOURCE_VERSION

    def worker() -> None:
        try:
            dashboard = fetch_crypto_dashboard(days)
            with state["lock"]:
                state["dashboard"] = dashboard
                state["running"] = False
                state["status"] = "Bitcoin data nactena."
                state["updated_at"] = pd.Timestamp.now().strftime("%d.%m.%Y %H:%M")
        except Exception as exc:
            with state["lock"]:
                state["running"] = False
                state["error"] = str(exc)
                state["status"] = "Bitcoin data se nepodarilo nacist."

    Thread(target=worker, daemon=True).start()
    return True


def ensure_crypto_preload_started() -> None:
    state = read_crypto_state()
    if state["running"] or state["dashboard"] is not None or state["error"]:
        return
    start_crypto_analysis()


def read_crypto_state() -> dict:
    state = get_crypto_state()
    with state["lock"]:
        if state.get("source_version") != CRYPTO_DATA_SOURCE_VERSION:
            state["running"] = False
            state["dashboard"] = None
            state["days"] = 365
            state["status"] = ""
            state["updated_at"] = ""
            state["error"] = None
            state["started_at"] = ""
            state["source_version"] = CRYPTO_DATA_SOURCE_VERSION

        return {
            "running": state["running"],
            "dashboard": state["dashboard"],
            "days": state["days"],
            "status": state["status"],
            "updated_at": state["updated_at"],
            "error": state["error"],
            "started_at": state["started_at"],
        }


def format_crypto_money(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"${value:,.2f}"


def format_crypto_large_money(value: float | None) -> str:
    if value is None:
        return "N/A"
    if abs(value) >= 1_000_000_000_000:
        return f"${value / 1_000_000_000_000:,.2f}T"
    if abs(value) >= 1_000_000_000:
        return f"${value / 1_000_000_000:,.2f}B"
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:,.2f}M"
    return f"${value:,.0f}"


def format_crypto_percent(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f} %"


def format_fee(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.0f} sat/vB"


def crypto_period_options() -> dict[str, int | str]:
    return {
        "30 dni": 30,
        "1 rok": 365,
        "5 let": 1825,
        "Maximum": "max",
    }


def build_drawdown_frame(history: pd.DataFrame) -> pd.DataFrame:
    if history.empty or "Cena BTC" not in history:
        return pd.DataFrame()
    price = history["Cena BTC"].dropna()
    if price.empty:
        return pd.DataFrame()
    drawdown = (price / price.cummax() - 1) * 100
    return pd.DataFrame({"Propad od maxima": drawdown})


def crypto_investor_table(market, sentiment, network) -> pd.DataFrame:
    rows = []
    if market is not None:
        rows.append(
            {
                "Oblast": "Cena vs. historie",
                "Hodnota": format_crypto_percent(market.ath_change_pct),
                "Proc je dulezite": "Ukazuje, jak daleko je BTC od historickeho maxima. Velky propad muze znamenat vetsi riziko i vetsi potencial, ale sam o sobe neni signal k nakupu.",
            }
        )
        rows.append(
            {
                "Oblast": "Likvidita trhu",
                "Hodnota": format_crypto_large_money(market.volume_24h_usd),
                "Proc je dulezite": "Vyssi objem znamena aktivnejsi trh. Pohyb ceny pri nizkem objemu muze byt mene presvedcivy.",
            }
        )
    if sentiment is not None:
        rows.append(
            {
                "Oblast": "Sentiment",
                "Hodnota": "N/A" if sentiment.value is None else f"{sentiment.value} / 100 ({sentiment.classification or 'N/A'})",
                "Proc je dulezite": "Extreme fear muze ukazovat paniku, extreme greed prehraty trh. Je to doplnek, ne samostatne pravidlo.",
            }
        )
    if network is not None:
        rows.append(
            {
                "Oblast": "Poplatky v siti",
                "Hodnota": format_fee(network.half_hour_fee),
                "Proc je dulezite": "Vyssi fee obvykle znamena vyssi poptavku po blockspace. Pro investora je to signal aktivity site, ne primo oceneni BTC.",
            }
        )
        rows.append(
            {
                "Oblast": "Mempool",
                "Hodnota": "N/A" if network.mempool_count is None else f"{network.mempool_count:,} transakci",
                "Proc je dulezite": "Plnejsi mempool ukazuje, ze vice transakci ceka na potvrzeni. Pomaha cist aktualni zatizeni bitcoinove site.",
            }
        )
    return pd.DataFrame(rows)


def whale_transactions_frame(whales, minimum_btc: float) -> pd.DataFrame:
    if whales is None:
        return pd.DataFrame()
    rows = []
    for transaction in whales.transactions:
        if transaction.total_output_btc < minimum_btc:
            continue
        rows.append(
            {
                "Cas": transaction.timestamp or "N/A",
                "Blok": "N/A" if transaction.block_height is None else f"{transaction.block_height:,}",
                "Objem BTC": f"{transaction.total_output_btc:,.2f}",
                "Fee BTC": "N/A" if transaction.fee_btc is None else f"{transaction.fee_btc:.8f}",
                "Vystupu": str(transaction.output_count),
                "TXID": transaction.txid,
                "Odkaz": transaction.link,
            }
        )
    return pd.DataFrame(rows)


def render_onchain_volume_section(onchain_volume) -> None:
    if onchain_volume is None:
        return

    st.markdown("---")
    st.markdown("### Rocni on-chain objemy")
    if not onchain_volume.estimated_volume.empty:
        st.line_chart(onchain_volume.estimated_volume, use_container_width=True, height=300)
        st.write(
            "Odhadovany prevod BTC sleduje, kolik Bitcoinu se denne ekonomicky presunulo po siti. "
            "Spicky v grafu mohou ukazovat vetsi aktivitu velkych ucastniku, burz nebo interni presuny. "
            "Tricetidenni prumer pomaha odfiltrovat jednodenne vykyvy."
        )
    else:
        st.info("Rocni odhadovany on-chain objem neni momentalne dostupny.")

    if not onchain_volume.output_volume.empty:
        with st.expander("Zobrazit celkovy vystupni objem BTC", expanded=False):
            st.line_chart(onchain_volume.output_volume, use_container_width=True, height=260)
            st.write(
                "Vystupni objem zahrnuje vsechny vystupy transakci vcetne change adres, proto muze byt vyrazne vyssi. "
                "Je uzitecny hlavne jako doplnkovy signal celkoveho zatizeni blockchainu."
            )


def render_bitcoin_dashboard(dashboard) -> None:
    for error in dashboard.errors:
        st.warning(error)

    market = dashboard.market
    network = dashboard.network
    sentiment = dashboard.sentiment
    whales = getattr(dashboard, "whales", None)
    onchain_volume = getattr(dashboard, "onchain_volume", None)

    if market is not None:
        st.markdown("### Bitcoin trh")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Cena BTC", format_crypto_money(market.price_usd), format_crypto_percent(market.price_change_24h_pct))
        m2.metric("Market cap", format_crypto_large_money(market.market_cap_usd))
        m3.metric("Objem 24h", format_crypto_large_money(market.volume_24h_usd))
        m4.metric("Propad od ATH", format_crypto_percent(market.ath_change_pct))

        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Zmena 7d", format_crypto_percent(market.price_change_7d_pct))
        p2.metric("Zmena 30d", format_crypto_percent(market.price_change_30d_pct))
        p3.metric("Zmena 1 rok", format_crypto_percent(market.price_change_1y_pct))
        p4.metric("ATH", format_crypto_money(market.ath_usd))

        if not market.history.empty:
            st.line_chart(market.history, use_container_width=True, height=340)
            drawdown_frame = build_drawdown_frame(market.history)
            if not drawdown_frame.empty:
                st.markdown("### Propad od maxima")
                st.line_chart(drawdown_frame, use_container_width=True, height=260)
                st.write(
                    "Drawdown ukazuje, o kolik procent je Bitcoin pod dosavadnim maximem v danem obdobi. "
                    "Pro dlouhodobeho investora je to uzitecnejsi pohled na riziko nez samotna cena."
                )
        st.write(
            "Trzni cast ukazuje, kde je Bitcoin cenove vuci poslednim obdobim a jak daleko je od historickeho maxima. "
            "Objem 24h pomaha cist, jestli se pohyb deje pri vyssi nebo nizsi aktivite trhu."
        )

        investor_frame = crypto_investor_table(market, sentiment, network)
        if not investor_frame.empty:
            st.markdown("### Co sledovat jako investor")
            st.dataframe(investor_frame, use_container_width=True, hide_index=True, height=260)

    if sentiment is not None:
        st.markdown("---")
        st.markdown("### Sentiment trhu")
        s1, s2 = st.columns(2)
        s1.metric("Fear & Greed Index", "N/A" if sentiment.value is None else str(sentiment.value), sentiment.classification or "N/A")
        s2.metric("Datum sentimentu", sentiment.updated_at or "N/A")
        if not sentiment.history.empty:
            chart = sentiment.history[["Fear & Greed"]]
            st.line_chart(chart, use_container_width=True, height=260)
        st.write(
            "Fear & Greed index meri naladu trhu od 0 do 100. Nizke hodnoty ukazuji strach, vysoke hodnoty chamtivost. "
            "Je to sentimentovy doplnek k cene, ne samostatny signal k nakupu nebo prodeji."
        )

    if network is not None:
        st.markdown("---")
        st.markdown("### Bitcoin sit")
        n1, n2, n3, n4 = st.columns(4)
        n1.metric("Nejrychlejsi fee", format_fee(network.fastest_fee))
        n2.metric("Fee do 30 min", format_fee(network.half_hour_fee))
        n3.metric("Fee do 60 min", format_fee(network.hour_fee))
        n4.metric("Posledni blok", "N/A" if network.tip_height is None else f"{network.tip_height:,}")

        q1, q2, q3 = st.columns(3)
        q1.metric("Transakce v mempoolu", "N/A" if network.mempool_count is None else f"{network.mempool_count:,}")
        q2.metric("Mempool vsize", "N/A" if network.mempool_vsize is None else f"{network.mempool_vsize:,.0f} vB")
        q3.metric("Mempool fee celkem", "N/A" if network.mempool_total_fee is None else f"{network.mempool_total_fee:,.0f} sats")
        st.write(
            "Sitova cast ukazuje, jak je Bitcoin aktualne zatizeny. Vyssi poplatky a plnejsi mempool obvykle znamenaji vyssi poptavku po blokovem prostoru."
        )

    render_onchain_volume_section(onchain_volume)

    if whales is not None:
        st.markdown("---")
        st.markdown("### Velke on-chain pohyby")
        minimum_btc = st.selectbox(
            "Minimalni velikost transakce",
            options=[50.0, 100.0, 250.0, 500.0, 1000.0],
            index=1,
            format_func=lambda value: f"{value:,.0f} BTC",
            help="Filtruje velke bitcoinove transakce podle celkove hodnoty vystupu.",
        )
        whale_frame = whale_transactions_frame(whales, minimum_btc)
        st.caption(
            f"Prohledano {whales.scanned_blocks} poslednich bloku a {whales.scanned_transactions:,} transakci. "
            "Jde o velke on-chain presuny BTC, ne o potvrzeny nakup nebo prodej."
        )
        if whale_frame.empty:
            st.info("V prohledane casti poslednich bloku nejsou transakce nad zvolenym limitem.")
        else:
            st.dataframe(
                whale_frame,
                use_container_width=True,
                hide_index=True,
                height=360,
                column_config={
                    "Odkaz": st.column_config.LinkColumn("Odkaz", display_text="mempool"),
                    "TXID": st.column_config.TextColumn("TXID", width="medium"),
                    "Objem BTC": st.column_config.TextColumn("Objem BTC", width="small"),
                },
            )
        st.write(
            "Velky presun muze byt burzovni vklad, vyber, interni presun mezi penezenkami nebo skutecna zmena vlastnictvi. "
            "Bez labelu adres proto aplikace netvrdi, ze jde o nakup nebo prodej."
        )


@st.fragment(run_every=1)
def render_bitcoin_loading_view() -> None:
    state = read_crypto_state()
    if state["running"]:
        st.info("Bitcoin data se nacitaji na pozadi.")
        st.caption(state["status"])
        return

    if state["updated_at"] and st.session_state.crypto_last_completed_at != state["updated_at"]:
        st.session_state.crypto_last_completed_at = state["updated_at"]
        st.rerun()

    if state["error"]:
        st.warning(state["error"])


def render_crypto_analysis() -> None:
    st.subheader("Bitcoin")
    st.write(
        "Bitcoinovy prehled kombinuje trzni data, stav site a sentiment trhu. "
        "Slouzi pro rychlou orientaci investora, ne jako investicni doporuceni."
    )
    st.caption("Zdroje dat: CoinGecko, Yahoo Finance pres yfinance, mempool.space a Alternative.me. Pri chybejicich datech se zobrazi N/A.")

    period_options = crypto_period_options()
    selected_period_label = st.selectbox(
        "Obdobi grafu",
        options=list(period_options.keys()),
        index=1,
        help="Meni historicke obdobi pro cenovy graf a drawdown graf.",
    )
    selected_days = period_options[selected_period_label]

    state = read_crypto_state()
    period_changed = state["days"] != selected_days
    if (state["dashboard"] is None or period_changed) and not state["running"]:
        start_crypto_analysis(selected_days)
        st.session_state.crypto_last_completed_at = ""
        state = read_crypto_state()

    control1, control2 = st.columns([1, 3])
    with control1:
        if st.button("Obnovit krypto data", use_container_width=True):
            start_crypto_analysis(selected_days)
            st.session_state.crypto_last_completed_at = ""
            state = read_crypto_state()
    with control2:
        if state["updated_at"]:
            st.caption(f"Posledni nacteni: {state['updated_at']}")

    if state["running"]:
        render_bitcoin_loading_view()
        return

    if state["error"]:
        st.warning(state["error"])

    dashboard = state["dashboard"]
    if dashboard is None:
        st.info("Bitcoin data zatim nejsou dostupna.")
        return

    if any("binance" in error.lower() for error in dashboard.errors):
        start_crypto_analysis(selected_days)
        st.session_state.crypto_last_completed_at = ""
        st.info("Odstranuji stara Binance data z pameti a nacitam Bitcoin z aktualnich zdroju.")
        render_bitcoin_loading_view()
        return

    render_bitcoin_dashboard(dashboard)


def set_buffett_section(scope: str, section: str) -> None:
    st.session_state[f"{scope}_active_buffett_section"] = section


def render_buffett_section_navigation(scope: str) -> str:
    sections = ["Analyza", "Hromadna analyza", "Jak funguje Buffett Score"]
    state_key = f"{scope}_active_buffett_section"
    current_section = st.session_state.get(state_key, sections[0])
    if current_section not in sections:
        current_section = sections[0]
        st.session_state[state_key] = current_section

    columns = st.columns([1, 1, 1.35])
    for column, section in zip(columns, sections):
        with column:
            if st.button(
                section,
                use_container_width=True,
                type="primary" if section == current_section else "secondary",
                key=f"{scope}_section_{section}",
                on_click=set_buffett_section,
                args=(scope, section),
            ):
                current_section = section

    st.caption(f"Aktivni sekce: {current_section}")
    return current_section


def render_buffett_workspace(
    companies,
    scope: str,
    manual_placeholder: str,
    empty_list_file: str,
) -> None:
    company_options = {f"{company.ticker} | {company.name}": company.ticker for company in companies}

    st.subheader("Vyber akcie")
    control1, control2, control3, control4 = st.columns([2.2, 1.3, 1, 1])
    with control1:
        st.markdown("**Firma ze seznamu**")
        selected_label = st.selectbox(
            "Vyber firmu ze seznamu",
            options=list(company_options.keys()) if company_options else [],
            index=0 if company_options else None,
            placeholder=f"Nejprve dopln {empty_list_file}",
            label_visibility="collapsed",
            key=f"{scope}_company_select",
        )
    with control2:
        st.markdown("**Rucni ticker**")
        manual_ticker = st.text_input(
            "Nebo zadej ticker rucne",
            placeholder=manual_placeholder,
            label_visibility="collapsed",
            key=f"{scope}_manual_ticker",
        ).strip().upper()
    with control3:
        st.markdown("**Akce**")
        analyze_clicked = st.button(
            "Analyzovat",
            type="primary",
            use_container_width=True,
            key=f"{scope}_analyze_button",
            on_click=set_buffett_section,
            args=(scope, "Analyza"),
        )
    with control4:
        st.markdown("**Akce**")
        analyze_all_clicked = st.button(
            "Hromadna analyza",
            use_container_width=True,
            key=f"{scope}_batch_button",
            on_click=set_buffett_section,
            args=(scope, "Hromadna analyza"),
        )

    if analyze_clicked:
        st.session_state[f"{scope}_active_buffett_section"] = "Analyza"
    if analyze_all_clicked:
        st.session_state[f"{scope}_active_buffett_section"] = "Hromadna analyza"

    if scope == "us":
        st.caption("Zdroj dat: cena a zakladni trzni metriky z Yahoo Finance, ucetni vykazy americkych firem z SEC EDGAR API.")
    else:
        st.caption("Zdroj dat: Yahoo Finance pres knihovnu yfinance.")
    selected_ticker = manual_ticker or company_options.get(selected_label, "")
    selected_section = render_buffett_section_navigation(scope)

    analysis_key = f"{scope}_single_analysis"
    ticker_key = f"{scope}_single_ticker"

    if selected_section == "Analyza":
        if analyze_clicked:
            if not selected_ticker:
                st.warning("Vyber ticker ze seznamu nebo ho zadej rucne.")
            else:
                with st.spinner(f"Nacitam data pro {selected_ticker}..."):
                    snapshot = load_company_snapshot(selected_ticker, use_sec_statements=(scope == "us"))
                    st.session_state[analysis_key] = analyze_company(snapshot)
                    st.session_state[ticker_key] = selected_ticker

        if st.session_state[analysis_key] is None:
            st.info("Zatim tu neni analyza konkretni firmy. Vyber ticker vyse a klikni na `Analyzovat`.")
        else:
            render_single_analysis(st.session_state[analysis_key], scope)

    if selected_section == "Hromadna analyza":
        if analyze_all_clicked:
            if not companies:
                st.warning(f"Seznam firem je prazdny. Nejprve dopln `{empty_list_file}`.")
            else:
                started = start_batch_analysis(companies, scope)
                if started:
                    st.success("Hromadna analyza se spustila na pozadi.")
                else:
                    st.info("Hromadna analyza uz bezi.")

        render_batch_analysis(scope)

    if selected_section == "Jak funguje Buffett Score":
        render_score_explanation()


def main() -> None:
    st.markdown(
        (
            "<div style='padding-top:1.1rem; display:flex; align-items:center; gap:0.75rem; margin:0 0 0.5rem 0;'>"
            "<h1 style='margin:0; padding:0; line-height:1.1;'>Buffett Analyzer</h1>"
            f"<span style='font-size:0.86rem; color:#6b7280; margin-top:0.2rem;'>v{APP_VERSION}</span>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )
    ensure_session_state()
    ensure_macro_preload_started()
    ensure_crypto_preload_started()

    companies = load_companies(Path(__file__).with_name("companies.txt"))
    cz_companies = load_companies(Path(__file__).with_name("companies_cz.txt"))
    buffett_tab, buffet_cz_tab, macro_tab, crypto_tab, crisis_tab, czech_tab = st.tabs(
        ["Buffett analyza", "Buffet CZ", "Makroekonomika (FRED)", "Bitcoin", "Krize", "ČR"]
    )

    with buffett_tab:
        render_buffett_workspace(companies, "us", "Napr. AAPL", "companies.txt")

    with buffet_cz_tab:
        render_buffett_workspace(cz_companies, "cz", "Napr. CEZ.PR", "companies_cz.txt")

    with macro_tab:
        render_macro_analysis()

    with crypto_tab:
        render_crypto_analysis()

    with crisis_tab:
        render_crisis_analysis()

    with czech_tab:
        render_czech_republic_analysis()


if __name__ == "__main__":
    main()
