# Odwołania2JSON — Urząd Zamówień Publicznych / HackNation 2026

**Od dokumentów do danych. Precyzyjna, deterministyczna i odporna transformacja spraw odwoławczych KIO do kanonicznego modelu JSON.**

Projekt realizuje zadanie automatycznej analizy i ekstrakcji informacji z akt spraw Krajowej Izby Odwoławczej (KIO) dostarczonych w formacie PDF (odwołanie, pełnomocnictwo, potwierdzenie przelewu bankowego) do plików JSON ściśle zgodnych ze schematami Urzędu Zamówień Publicznych (`oczekiwane-json`).

---

## 🏆 Wyniki ewaluacji F1 (60 spraw treningowych, 180 dokumentów)

Wynik mierzony oficjalnym skryptem `evaluate.py` zgodnie z definicją metryki UZP:
- **TP (True Positive):** wartość zgodna z ground truth (dla długich stringów: podobieństwo `difflib.SequenceMatcher >= 0.80`).
- **TN (True Negative):** wartość `null` w obu źródłach.
- **FP (False Positive):** wartość tylko w predykcji lub niezgodna z referencją.
- **FN (False Negative):** brak wartości w predykcji, gdy występuje w referencji.
- **Wskaźnik F1:** \( F1 = \frac{2 \cdot TP}{2 \cdot TP + FP + FN} \)

Szczegółowy raport znajduje się w pliku [EVAL.md](EVAL.md):

| Typ dokumentu | Dokumenty | TP | TN | FP | FN | F1 Score |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **Odwołanie** | 60 | 1214 | 121 | 0 | 0 | **1,0000 (100%)** |
| **Pełnomocnictwo** | 60 | 458 | 163 | 0 | 0 | **1,0000 (100%)** |
| **Przelew bankowy** | 60 | 360 | 0 | 0 | 0 | **1,0000 (100%)** |
| **ŁĄCZNIE** | **180** | **2032** | **284** | **0** | **0** | **1,0000 (100%)** |

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
```
*Aplikacja działa w pełni autonomicznie również bez podanego klucza (fallback na reguły deterministyczne).*

### Uruchomienie CLI (przetwarzanie wsadowe)

Aplikacja przetwarza katalog wejściowy `input` i zapisuje pliki JSON o analogicznych nazwach w katalogu `output`:

```bash
python app.py input output
```

Tryb bez LLM (czysty regex + heurystyka):
```bash
python app.py input output --no-llm
```

### Uruchomienie interfejsu webowego (GUI)

```bash
python app.py --serve --port 8000
```
Interfejs dostępny jest pod adresem: `http://localhost:8000`. Umożliwia przeciągnięcie pliku PDF, podgląd dokumentu oraz wygenerowanego JSON-a.

---

## ⚙️ Architektura i działanie systemu

1. **Warstwa ekstrakcji tekstu i OCR:**
   - Podstawowa ekstrakcja za pomocą biblioteki PyMuPDF (`fitz`).
   - Automatyczna detekcja skanów: jeśli strona zawiera poniżej 200 znaków, uruchamiany jest Tesseract OCR (`pol`). W przypadku braku Tesseracta zwracane są bezpieczne wartości `null`.
2. **Czterostanowy klasyfikator dokumentów:**
   - Klasyfikacja bazuje na ważonych sygnałach słownikowych z `config/document_types.yaml`: `odwolanie`, `pelnomocnictwo`, `przelew`, `nieznany`.
   - **Obsługa dokumentów obcych (`nieznany`):** jeśli dokument nie należy do żadnej ze wspieranych kategorii spraw KIO, generowany jest **pusty JSON `{}`** o nazwie pliku wejściowego. Nie powoduje to przerwania procesu przetwarzania wsadowego.
3. **Ekstrakcja deterministyczna (Zero Hallucination):**
   - Pola jednoznaczne wyciągane są w 100% deterministycznie z walidacją formatu:
     - Numery identyfikacyjne: NIP (10 cyfr), KRS (10 cyfr), REGON (9 lub 14 cyfr).
     - Numery bankowe: IBAN (26 cyfr, normalizacja formatu PL).
     - Identyfikatory postępowań: numery referencyjne spraw, ogłoszenia BZP i TED (kanoniczny format `XXXXXX-YYYY`).
     - Kwoty i daty transakcji (eliminacja mylenia daty operacji z datą generowania wyciągu).
     - Konsorcja i reprezentacja: prawidłowe rozdzielanie członków konsorcjum (Lider vs Partner) oraz podpisujących.
4. **Hierarchia silników LLM (wyłącznie Open Source / dostawca z UE):**
   - Zgodnie z wytycznymi UZP hosting prywatny/lokalny jest wykluczony. W runtime **nie są używane** modele komercyjne/zamknięte (brak Gemini, Claude).
   - Kolejność providerów w runtime:
     1. **Primary:** Open-source model po API z dostawcą z UE (**Mistral AI**, model open-weights `mistral-small-latest`). Serwery zlokalizowane na terenie UE/EOG, dostawca posiadający Politykę Prywatności określającą zakres i miejsce przetwarzania danych.
     2. **Fallback:** Pełny tryb deterministyczny (regex + heurystyka). Gwarantuje niezawodność, odporność na limity API i brak opóźnień sieciowych.
   - LLM jest wywoływany **wyłącznie jako fallback do pól niejednoznacznych** (np. `czynnosc_powod_id` 1–12 z pierwszego zarzutu, tytuł zamówienia), gdy reguły deterministyczne napotkają brak danych.
5. **Moduł spójności sprawy (`case_consistency.py`):**
   - Zgodnie z zasadą *„Zrozum sprawę. Nie tylko dokument.”* po przetworzeniu kompletu dokumentów sprawy następuje walidacja i uzupełnianie braków krzyżowych (np. weryfikacja NIP odwołującego vs mocodawcy, numeru postępowania w tytule przelewu).
6. **Ścisła zgodność ze schematami UZP:**
   - Wygenerowany JSON jest ściśle dopasowany do schematów `odwolanie.json`, `pelnomocnictwo.json`, `przelew.json` (właściwe typy pól, brak cytowań w JSON, brak dodatkowych nieudokumentowanych kluczy).

---

## 📁 Struktura projektu

```text
├── app.py                      # Punkt wejściowy CLI oraz serwer GUI
├── extractor.py                # Pipeline OCR, klasyfikator, parser dokumentów
├── case_consistency.py         # Walidacja i spójność między dokumentami sprawy
├── llm.py                      # Klient LLM (Mistral API UE -> regex fallback)
├── evaluate.py                 # Oficjalny skrypt ewaluacji F1 wg wytycznych UZP
├── config/
│   └── document_types.yaml     # Konfiguracja klas dokumentów i słowników
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
