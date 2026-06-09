# Buffett Analyzer

Buffett Analyzer je jednoduchá Streamlit aplikace pro osobní fundamentální analýzu amerických akcií podle principů Warrena Buffetta.

## Vlastnosti

- Načítá data pouze z Yahoo Finance přes `yfinance`
- Nevymýšlí žádné finanční hodnoty
- Chybějící data zobrazuje jako `N/A`
- Zobrazuje varování při neúplných datech
- Ukazuje základní Buffett-style skóre na základě dostupných metrik

## Struktura projektu

```text
app.py
analyzer.py
company_loader.py
data_provider.py
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
pip install -r requirements.txt
```

## Spuštění

```bash
streamlit run app.py
```

## Poznámky

- Pokud Yahoo Finance některé pole neposkytne, aplikace zobrazí `N/A`.
- Výstup je určen pro osobní analýzu, nejde o investiční doporučení.
- Hlavní specifikace projektu je v `docs/BUFFETT_ANALYZER_SPEC.md`.
