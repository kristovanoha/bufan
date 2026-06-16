# Buffett Analyzer

Buffett Analyzer je jednoducha Streamlit aplikace pro osobni fundamentalni analyzu americkych akcii podle principu Warrena Buffetta.

## Vlastnosti

- Trzni cenu a zakladni trzni metriky nacita z Yahoo Finance pres `yfinance`
- Ucetni vykazy americkych firem nacita z oficialniho SEC EDGAR API
- Insider transakce aktualniho vedeni za 5 let zobrazuje z oficialnich SEC Form 4 a 5
- Nic nevymysli: chybejici hodnoty zobrazuje jako `N/A`
- Zobrazuje varovani pri neuplnych nebo nedostupnych datech
- Ukazuje Buffett-style skore na zaklade kvality firmy, cash flow, zadluzeni a ceny
- Pocita orientacni vnitrni hodnotu pomoci owner earnings DCF

## Verze aplikace

- Aktualni verze aplikace je zapsana v `app.py` v konstante `APP_VERSION`.
- Aktualne nasazena verze v tomto repozitari: `1.4.3`
- Pri kazde zmene aplikace je povinne cislo verze zvysit, aby bylo v UI jasne videt, co je nasazene.

## Struktura projektu

```text
app.py
analyzer.py
company_loader.py
data_provider.py
sec_edgar_provider.py
sec_insider_provider.py
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

## Spusteni

```bash
python -m streamlit run app.py
```

## Zdroje dat

- Yahoo Finance pres `yfinance`: cena akcie, market cap, trailing P/E, dividendovy vynos a dalsi rychle trzni metriky
- SEC EDGAR API: oficialni ucetni vykazy americkych firem pro revenue, net income, cash, debt, equity, operating cash flow a dalsi odvozene pomery
- SEC Form 3/4/5: insider transakce vedeni, reditelu a 10% vlastniku

## Poznamky

- Pokud Yahoo Finance nebo SEC nektere pole neposkytnou, aplikace zobrazi `N/A`.
- Vnitrni hodnota je odhad, ne presna hodnota. Model pouziva Free Cash Flow jako dostupnou aproximaci owner earnings.
- Nakupni cena je vnitrni hodnota snizena o 25% margin of safety.
- Vystup je urcen pro osobni analyzu, nejde o investicni doporuceni.
- Volitelne muzes do `config.json` nebo do promenne prostredi `SEC_USER_AGENT` doplnit vlastni User-Agent pro SEC pozadavky.
- Hlavni specifikace projektu je v `docs/BUFFETT_ANALYZER_SPEC.md`.
