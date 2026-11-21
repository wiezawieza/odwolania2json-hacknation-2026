# Odwołania2JSON — Urząd Zamówień Publicznych / HackNation 2026

**Od dokumentów do danych. Precyzyjna, deterministyczna i odporna transformacja spraw odwoławczych KIO do kanonicznego modelu JSON.**

Projekt realizuje zadanie automatycznej analizy i ekstrakcji informacji z akt spraw Krajowej Izby Odwoławczej (KIO) dostarczonych w formacie PDF (odwołanie, pełnomocnictwo, potwierdzenie przelewu bankowego) do plików JSON ściśle zgodnych ze schematami Urzędu Zamówień Publicznych (`oczekiwane-json`).

---

## 🏆 Wyniki ewaluacji F1 (210 dokumentów: 60 spraw treningowych + 30 skanów UZP)

Wynik mierzony oficjalnym skryptem `evaluate.py` zgodnie z definicją metryki UZP:
- **TP (True Positive):** wartość zgodna z ground truth (dla długich stringów: podobieństwo `difflib.SequenceMatcher >= 0.80`).
- **TN (True Negative):** wartość `null` w obu źródłach.
- **FP (False Positive):** wartość tylko w predykcji lub niezgodna z referencją.
- **FN (False Negative):** brak wartości w predykcji, gdy występuje w referencji.
- **Wskaźnik F1:** \( F1 = \frac{2 \cdot TP}{2 \cdot TP + FP + FN} \)

Szczegółowy raport znajduje się w pliku [EVAL.md](EVAL.md):

| Zbiór danych | Typy dokumentów | Liczba dok. | TP | TN | FP | FN | F1 Score |
|---|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **Tekstowe PDF (60 spraw)** | Odwołanie, Pełnomocnictwo, Przelew | 180 | 2032 | 284 | 0 | 0 | **1,0000 (100%)** |
| **Skany syntetyczne (OCR)** | Odwołanie, Pełnomocnictwo, Przelew | 30 | 309 | 79 | 0 | 0 | **1,0000 (100%)** |
| **ŁĄCZNIE (Wszystkie zbiory)** | **Pełny zestaw referencyjny UZP** | **210** | **2341** | **363** | **0** | **0** | **1,0000 (100%)** |

### Wyniki per typ dokumentu (na całym zbiorze 210 dokumentów):
- **Odwołanie (70 dokumentów):** F1 = **1,0000** (TP=1391, TN=169, FP=0, FN=0)
- **Pełnomocnictwo (70 dokumentów):** F1 = **1,0000** (TP=536, TN=188, FP=0, FN=0)
- **Przelew bankowy (70 dokumentów):** F1 = **1,0000** (TP=414, TN=6, FP=0, FN=0)
- **Dokumenty negatywne (16 PDF-ów bez ground truth, w tym postanowienie KIO):** 16/16 → pusty JSON `{}` (raportowane osobno przez `evaluate.py`).

> Wynik jest w pełni deterministyczny: na zbiorze treningowym LLM nie jest wywoływany ani razu (reguły wypełniają wszystkie pola; sprawdzone uruchomieniem z niedostępnym endpointem API).

> **Zasada UZP (15:55) — Mianownik (casus nominativus):** Wszystkie pola `imie_nazwisko` (pełnomocnicy, mocodawca, podpisujący) są normalizowane do formy mianownikowej. Priorytet ma mianownik z dokumentu (pieczęć, blok podpisu pod kropkami `.....`, formuła przyjęcia pełnomocnictwa, lista radców). Formy odmienione podlegają normalizacji przez LLM (Ministral 3 3B, Mistral API z UE) z walidacją regułową; brak możliwości sprowadzenia do mianownika skutkuje zwróceniem `null` (bezwzględny zakaz kopiowania form deklinacyjnych).

> **Brak overfittingu:** Rozwiązanie przeszło test odporności na zmodyfikowanych, syntetycznych danych (zmienione nazwy podmiotów, przesunięte daty, nowe numery spraw, BZP i TED), zachowując wynik **F1 = 1,0000**. W kodzie nie ma żadnych zahardkodowanych wartości ze zbioru treningowego.

---

## 🚀 Szybki start i uruchomienie

### Wymagania
- **Python 3.13+**
- Opcjonalnie: Tesseract OCR z pakietem języka polskiego (`tesseract-ocr-pol`) dla skanów bez warstwy tekstowej.

### Instalacja

```bash
# 1. Klonowanie repozytorium
git clone https://github.com/wiezawieza/odwolania2json-hacknation-2026.git
cd odwolania2json-hacknation-2026

# 2. Utworzenie i aktywacja środowiska wirtualnego
python -m venv .venv
# Linux / macOS:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate

# 3. Instalacja zależności
pip install -r requirements.txt
```

### Konfiguracja API LLM (opcjonalna)

Skopiuj plik `.env.example` do `.env` i uzupełnij klucz API Mistral:
```bash
cp .env.example .env
```
Zawartość `.env`:
```env
LLM_API_KEY=twoj_klucz_mistral
# LLM_MODEL=ministral-3b-2512   # domyślny, przypięty model (≤8B); nie używać aliasów -latest
```
*Aplikacja działa w pełni autonomicznie również bez podanego klucza (fallback na reguły deterministyczne).*

### Uruchomienie CLI (przetwarzanie wsadowe)

Aplikacja przetwarza katalog wejściowy `input` i zapisuje pliki JSON o analogicznych nazwach w katalogu `output`:

```bash
python app.py input output
```

Tryb bez LLM (czysty regex + heurystyka, zero wywołań API — także bez normalizacji nazwisk przez LLM):
```bash
python app.py input output --no-llm
```

**Integracja z pipeline'em:** każdy JSON jest walidowany przed zapisem (`validation.py`), a batch zapisuje raport `output_raport.json` + `output_raport.csv` (typ, status `OK` / `ODRZUCONO` / `INVALID` / `ERR`, czas każdego dokumentu, OCR, strony, błędy; ścieżka: `--report`). Exit code: `0` — wszystko przetworzone, `1` — co najmniej jeden błąd przetwarzania lub walidacji, `2` — brak wejścia. Szczegóły: [TECHNICAL.md](TECHNICAL.md).

### Zmierzony czas przetwarzania

Pełny zbiór treningowy (226 PDF-ów: 210 z ground truth + 16 negatywów), **cache OCR wyczyszczony** (każdy skan OCR-owany od zera), tryb domyślny, laptop Intel Core i7-6700HQ (4 rdzenie, 2016), 16 GB RAM, Tesseract `pol`. Wynik z raportu wsadowego (`czas_s` każdego dokumentu):

| Rodzaj dokumentu | Liczba | Średnio | Mediana | Maks. | Na stronę |
|---|:---:|:---:|:---:|:---:|:---:|
| PDF tekstowy | 184 | **0,010 s** | 0,006 s | 0,14 s | < 0,01 s |
| Skan (OCR Tesseract) | 33 | **8,9 s** | 2,8 s | 30,4 s | 3,7 s |
| Cały zbiór (226) | 226 | 1,3 s | — | — | — |

Łącznie 295 s; F1 w tym przebiegu = 1,0000, negatywy 16/16. Czas skanu zależy głównie od liczby stron (OCR ok. 3,7 s/stronę). Ponowne przetworzenie tego samego skanu korzysta z cache OCR (`.cache_ocr/`, klucz: treść pliku) — ok. 0,03 s.

### Uruchomienie interfejsu webowego (GUI)

```bash
python app.py --serve --port 8000
```
Interfejs dostępny jest pod adresem: `http://localhost:8000` (bez zewnętrznych zależności, działa na Pythonie 3.13+):
- **Dokument** — podgląd PDF obok wyniku. Każde pole JSON-a ma **pochodzenie**: metodę (`regex` / `heurystyka` / `LLM` / `null — brak danych` / `spójność sprawy`), numer strony (klik przenosi podgląd PDF na tę stronę) i fragment tekstu źródłowego z podświetloną wartością. Metoda „LLM” to fakt, nie deklaracja: pole różni się od wyniku tego samego dokumentu przetworzonego z wyłączonym LLM-em. Metadane pochodzenia **nie trafiają do JSON-a** — „Pobierz JSON” zwraca wyłącznie model UZP.
- **Wyjaśnienia dla urzędnika** — przy każdym polu nota „dlaczego taka wartość” (reguła, strona, fragment) albo „dlaczego null” (czego szukano, np. „szukano: 11 cyfr po „PESEL” przy osobie — nie znaleziono”; „mocodawcą jest firma — imię i nazwisko nie dotyczy”).
- **Nowy typ dokumentu (kreator)** — formularz bez pisania kodu: identyfikator i nazwa typu, słowa kluczowe z wagami (rozpoznawanie), pola JSON-a (ścieżka z kropkami = zagnieżdżenie) z regułą dla każdego pola wybraną z budulców silnika (data/miejscowość z nagłówka, nazwa/NIP/KRS/REGON/PESEL strony, numer postępowania, własny regex, wybór z listy, podpisujący, stała). **Testuj na PDF-ie** pokazuje punktację klasyfikatora (czy PDF zostanie rozpoznany jako nowy typ), wynikowy JSON i pochodzenie każdego pola — bez LLM-a. **Zapisz typ** tworzy `config/custom/<id>.yaml`; typ działa od razu w GUI i w CLI (wczytywany przez `load_config`, nie nadpisuje klas z `document_types.yaml`). **Pobierz YAML** — gotowy wpis do przeglądu/commita. Formularz waliduje dane (zajęte nazwy, wagi, poprawność regexów, kolizje ścieżek) i zwraca błędy po polsku.
- **Sprawa** — kilka PDF-ów jednej sprawy (typ rozpoznawany po treści) → jeden scalony podgląd (odwołujący, zamawiający, numer postępowania, kwota wpisu — przy każdej wartości dokument źródłowy) oraz kontrole spójności z wynikiem **OK / UWAGA**: NIP odwołującego vs mocodawcy, kwota przelewu vs wpis od odwołania (rozróżnia przelew tytułem wpisu od opłaty skarbowej od pełnomocnictwa), numer postępowania w tytule przelewu, pełnomocnik z odwołania na liście pełnomocników. Te same kontrole wypisuje tryb wsadowy CLI.

---

## ⚙️ Architektura i działanie systemu

1. **Warstwa ekstrakcji tekstu i OCR:**
   - Podstawowa ekstrakcja za pomocą biblioteki PyMuPDF (`fitz`).
   - Automatyczna detekcja skanów: jeśli strona zawiera poniżej 200 znaków, uruchamiany jest Tesseract OCR (`pol`). W przypadku braku Tesseracta zwracane są bezpieczne wartości `null`.
2. **Klasyfikator i elastyczność dodawania typów (Config-Only Extensibility):**
   - Klasyfikacja bazuje na ważonych sygnałach słownikowych z `config/document_types.yaml`: `odwolanie`, `pelnomocnictwo`, `przelew`, `przystapienie`, `nieznany`. Sygnały negatywne (`negative_keywords`, np. „orzeka:”, „po rozpoznaniu na posiedzeniu”) odejmują punkty — orzeczenia KIO, które używają słownictwa odwołań, trafiają do `nieznany`.
   - **Nowe typy dokumentów dodaje się bez ingerencji w kod źródłowy.** Trzy klasy bazowe mają dedykowane ekstraktory; każda nowa klasa jest ekstrahowana wyłącznie według sekcji `extract` w YAML: zagnieżdżenie kluczy = struktura wynikowego JSON-a, każdy liść = reguła z gotowych budulców silnika (`header_date`, `header_place`, `party_name`, `party_id`, `numer_postepowania`, `regex`, `choice`, `signers`, `const`). Flaga `llm: true` pozwala LLM-owi uzupełnić pole puste po regułach — wartość jest przyjmowana tylko po walidacji formatu i gdy występuje w tekście dokumentu. Klasa z samą listą `fields` (bez reguł) działa w trybie „tylko LLM” z tą samą walidacją.
   - Zweryfikowane na 4. typie — zgłoszenie przystąpienia do postępowania odwoławczego (art. 524 Pzp): w kodzie nie ma żadnej linii specyficznej dla tego typu, całość to sekcja `przystapienie` w YAML; **F1 = 1,0000** na przykładach w `examples/przystapienie/`.
   - **Obsługa dokumentów obcych (`nieznany`):** jeśli dokument nie należy do żadnej ze wspieranych kategorii spraw KIO, generowany jest **pusty JSON `{}`** o nazwie pliku wejściowego (także w trybie pojedynczego pliku i przy błędzie odczytu PDF-a). Nie powoduje to przerwania procesu przetwarzania wsadowego. Dokument obcy to status `ODRZUCONO` (poprawny wynik), a plik nieczytelny (nie-PDF, brak tekstu, skan bez dostępnego OCR) to status `ERR` w raporcie i exit code 1.
3. **Ekstrakcja deterministyczna (Zero Hallucination):**
   - Pola jednoznaczne wyciągane są w 100% deterministycznie z walidacją formatu:
     - Numery identyfikacyjne: NIP (10 cyfr), KRS (10 cyfr), REGON (9 lub 14 cyfr).
     - Numery bankowe: IBAN (26 cyfr, normalizacja formatu PL).
     - Identyfikatory postępowań: numery referencyjne spraw, ogłoszenia BZP i TED (kanoniczny format `XXXXXX-YYYY`).
     - Kwoty i daty transakcji (eliminacja mylenia daty operacji z datą generowania wyciągu).
     - Konsorcja i reprezentacja: prawidłowe rozdzielanie członków konsorcjum (Lider vs Partner) oraz podpisujących.
4. **Model LLM (wyłącznie Open Source / dostawca z UE):**
   - **Jeden model: Ministral 3 3B Instruct (`ministral-3b-2512`)** — open weights na licencji Apache 2.0 (`mistralai/Ministral-3-3B-Instruct-2512`), **3,4 mld parametrów** modelu językowego + **0,4 mld** enkodera obrazu = **3,8 mld parametrów razem** (wg karty modelu na Hugging Face) → kategoria **≤ 8B (20 pkt wg tabeli UZP)**. Model jest przypięty do wersji (`llm.py`, `.env.example`, `docker-compose.yml`); aliasy `*-latest` nie są używane. Wcześniej rozważany `ministral-8b-2410` (8,02 mld) jest wycofany z API Mistral (zapytanie jest po cichu kierowane na `ministral-8b-2512`, ok. 8,8 mld), dlatego z niego zrezygnowano.
   - Dostawca: **Mistral AI** (API z UE; Polityka Prywatności określa zakres i miejsce przetwarzania danych na terenie UE/EOG). Zgodnie z wytycznymi UZP hosting prywatny/lokalny jest wykluczony, a modele komercyjne/zamknięte (Gemini, Claude) **nie są używane** w runtime.
   - **Silnik główny jest deterministyczny** (regex + heurystyka + konfiguracja YAML): na 210 dokumentach treningowych oraz na przykładach `przystapienie` wykonano **0 wywołań LLM** (zmierzone licznikiem wywołań API przy włączonym LLM i obecnym kluczu), a F1 = 1,0000.
   - Kolejność w runtime: 1) reguły deterministyczne; 2) **LLM tylko jako fallback dla pól niejednoznacznych** (poniżej); 3) brak klucza / błąd API → `null` bez zgadywania.
   - LLM jest wywoływany **wyłącznie jako fallback do pól niejednoznacznych** (np. `czynnosc_powod_id` 1–12 z pierwszego zarzutu, tytuł zamówienia), gdy reguły deterministyczne napotkają brak danych. Odpowiedź jest walidowana: id spoza słownika 1–12, puste napisy i „null” → `null`; tytuł przyjmowany tylko, gdy jego słowa występują w dokumencie.
5. **Moduł spójności sprawy (`case_consistency.py`):**
   - Zgodnie z zasadą *„Zrozum sprawę. Nie tylko dokument.”* po przetworzeniu kompletu dokumentów sprawy następuje walidacja i uzupełnianie braków krzyżowych (np. weryfikacja NIP odwołującego vs mocodawcy, numeru postępowania w tytule przelewu), a kontrole OK / UWAGA (w tym kwota przelewu vs wpis od odwołania) są pokazywane w GUI i wypisywane w CLI.
   - **Pochodzenie wartości (`provenance.py`)** — dla GUI: metoda, strona i fragment tekstu źródłowego każdego pola (na 210 dokumentach treningowych lokalizacja w tekście dla 99,8% niepustych pól; pozostałe to pola wyliczane, np. `przez-pelnomocnika = 0`, opisane notą).
6. **Ścisła zgodność ze schematami UZP:**
   - Wygenerowany JSON jest ściśle dopasowany do schematów `odwolanie.json`, `pelnomocnictwo.json`, `przelew.json` (właściwe typy pól, brak cytowań w JSON, brak dodatkowych nieudokumentowanych kluczy).

---

## 📁 Struktura projektu

```text
├── app.py                      # Punkt wejściowy CLI oraz serwer GUI
├── extractor.py                # Pipeline OCR, klasyfikator, parser dokumentów
├── case_consistency.py         # Walidacja i spójność między dokumentami sprawy
├── provenance.py               # Pochodzenie wartości dla GUI (metoda, strona, fragment, dlaczego null)
├── wizard.py                   # Kreator nowego typu dokumentu (formularz GUI -> YAML -> test na PDF-ie)
├── validation.py               # Walidacja JSON przed zapisem (struktura klasy, formaty pól)
├── llm.py                      # Klient LLM (Ministral 3 3B przez Mistral API UE -> fallback deterministyczny)
├── TECHNICAL.md                # Opis techniczny: architektura i model LLM
├── evaluate.py                 # Oficjalny skrypt ewaluacji F1 wg wytycznych UZP
├── config/
│   ├── document_types.yaml     # Konfiguracja klas dokumentów i słowników
│   ├── custom/                 # (opcjonalnie) typy zapisane kreatorem w GUI
│   └── schemas/                # Schematy JSON dla klas dokumentów
├── examples/
│   └── przystapienie/          # Syntetyczne przykłady PDF + JSON dla typu przystapienie
├── static/
│   └── index.html              # Interfejs graficzny web GUI
├── EVAL.md                     # Raport z wynikami ewaluacji F1
├── requirements.txt            # Zależności open-source (Python 3.13+)
├── Dockerfile                  # Konteneryzacja środowiska z Tesseract OCR
├── docker-compose.yml          # Konfiguracja uruchomienia kontenerowego
├── .env.example                # Przykładowy plik zmiennych środowiskowych
└── README.md                   # Dokumentacja projektu
```

---

## 🧪 Uruchomienie ewaluacji

Aby zweryfikować wyniki F1 na zbiorze danych treningowych:

```bash
# 1. Przetworzenie danych treningowych do katalogu output
python app.py hacknation-uzp-repo/dane output

# 2. Uruchomienie skryptu ewaluacyjnego
python evaluate.py hacknation-uzp-repo/dane output
```
Raport zostanie wyświetlony w konsoli oraz zaktualizowany w pliku `EVAL.md`.
