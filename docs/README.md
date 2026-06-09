# Dokumentace projektu

Tento adresář obsahuje podklady pro vývoj aplikace **Buffett Analyzer**.

## Co má program dělat

Program bude sloužit pro osobní fundamentální analýzu amerických akcií inspirovanou principy Warrena Buffetta.
Má zobrazovat pouze reálná finanční data získaná z Yahoo Finance přes knihovnu `yfinance`.

## Jak se má program chovat

- Pokud je finanční hodnota dostupná, aplikace ji zobrazí uživateli.
- Pokud hodnota v datech chybí, aplikace zobrazí `N/A`.
- Při chybějících důležitých údajích aplikace zároveň zobrazí varování.
- Aplikace bude postavena v Pythonu se Streamlit rozhraním.

## Jak tento adresář používat

- Soubor `BUFFETT_ANALYZER_SPEC.md` je hlavní zadání pro implementaci.
- Tento `README.md` stručně vysvětluje účel projektu a základní pravidla.
- Další návrhové nebo technické dokumenty je vhodné ukládat sem, aby byly oddělené od zdrojového kódu.
