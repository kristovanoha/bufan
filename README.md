# Buffett Analyzer

Buffett Analyzer je jednoduchá Streamlit aplikace pro osobní fundamentální analýzu amerických akcií podle principů Warrena Buffetta.

## Vlastnosti

- Tržní cenu a základní tržní metriky načítá z Yahoo Finance přes `yfinance`
- Účetní výkazy amerických firem načítá z oficiálního SEC EDGAR API
- Nic nevymýšlí: chybějící hodnoty zobrazuje jako `N/A`
- Zobrazuje varování při neúplných nebo nedostupných datech
- Ukazuje Buffett-style skóre na základě kvality firmy, cash flow, zadlužení a ceny
- Počítá orientační vnitřní hodnotu pomocí owner earnings DCF

## Verze aplikace

- Aktuální verze aplikace je zapsaná v `app.py` v konstantě `APP_VERSION`.
- Aktuálně nasazená verze v tomto repozitáři: `1.2.1`
- Při každé změně aplikace je povinné číslo verze zvýšit, aby bylo v UI jasně vidět, co je nasazené.

## Struktura projektu

```text
app.py
analyzer.py
company_loader.py
data_provider.py
sec_edgar_provider.py
scoring.py
models.py
companies.txt
requirements.txt
README.md
docs/
AGENTS.md
```

## Instalace

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

## Spuštění

```bash
python -m streamlit run app.py
```

## Zdroje dat

- Yahoo Finance přes `yfinance`: cena akcie, market cap, trailing P/E, dividendový výnos a další rychlé tržní metriky
- SEC EDGAR API: oficiální účetní výkazy amerických firem pro revenue, net income, cash, debt, equity, operating cash flow a další odvozené poměry

## Poznámky

- Pokud Yahoo Finance nebo SEC některé pole neposkytnou, aplikace zobrazí `N/A`.
- Vnitřní hodnota je odhad, ne přesná hodnota. Model používá Free Cash Flow jako dostupnou aproximaci owner earnings.
- Nákupní cena je vnitřní hodnota snížená o 25% margin of safety.
- Výstup je určen pro osobní analýzu, nejde o investiční doporučení.
- Volitelně můžeš do `config.json` nebo do proměnné prostředí `SEC_USER_AGENT` doplnit vlastní User-Agent pro SEC požadavky.
- Hlavní specifikace projektu je v `docs/BUFFETT_ANALYZER_SPEC.md`.
