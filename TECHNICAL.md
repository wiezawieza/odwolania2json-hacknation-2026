# Dokumentacja techniczna — Odwołania2JSON

Opis architektury, modelu LLM i rozszerzalności. Uruchomienie: [README.md](README.md) i [RUN.md](RUN.md) (VM ewaluacyjna),
raport F1: [EVAL.md](EVAL.md).

## Architektura (skrót)

PDF → ekstrakcja tekstu (warstwa tekstowa, a dla skanów OCR Tesseract `pol`) → klasyfikacja typu dokumentu
(sygnały słownikowe w `config/document_types.yaml`) → ekstraktor deterministyczny (regex + heurystyka;
nowe typy dokumentów wyłącznie przez sekcję `extract` w YAML) → walidacja formatu i schematu UZP →
walidacja spójności sprawy (`case_consistency.py`) → JSON. Dokumenty nierozpoznane i negatywne dają `{}`.

## Model LLM — pełna specyfikacja

| Parametr | Wartość |
|---|---|
| Liczba modeli | **jeden** — ten sam model obsługuje każde wywołanie LLM-a |
| Model | **Ministral 3 3B Instruct 2512**, identyfikator API `ministral-3b-2512` (przypięty do wersji, bez aliasów `*-latest`) |
| Rozmiar | **3,4 mld** parametrów modelu językowego + **0,4 mld** enkodera obrazu = **3,8 mld parametrów razem** (karta modelu na Hugging Face) |
| Architektura | gęsta (**dense**), nie MoE — nie ma podziału na parametry „aktywne” i „całkowite”; 3,8 mld to pełny rozmiar modelu |
| Licencja / wagi | **Apache 2.0**, otwarte wagi: `mistralai/Ministral-3-3B-Instruct-2512` |
| Dostawca | **Mistral AI** (Francja), serwery w UE; Polityka Prywatności określa zakres i miejsce przetwarzania danych (UE/EOG) |
| Dostęp | **bezpośrednio przez API Mistral** (`https://api.mistral.ai/v1/chat/completions`), bez pośredników (bez OpenRoutera), bez hostingu prywatnego |
| Kategoria wg tabeli UZP | ≤ 8B parametrów |
| Konfiguracja | `LLM_API_KEY` (lub `MISTRAL_API_KEY`) w `.env` na serwerze; opcjonalnie `LLM_API_BASE`, `LLM_MODEL` (domyślnie `ministral-3b-2512`) |
| Parametry wywołania | temperatura 0, prompt systemowy wymusza odpowiedź JSON-em i `null` zamiast zmyślania |

**Silnik główny jest deterministyczny** (regex + heurystyka + konfiguracja YAML). Na 210 dokumentach treningowych,
16 negatywach i 3 przykładach `przystapienie` wykonano **0 wywołań LLM** (przebieg z adresem API ustawionym na
nieistniejący endpoint: każde wywołanie zostawiłoby w logu linię `[llm] …`, a w logu nie ma żadnej). To samo dotyczy
skanów OCR-owanych w obrazie Docker (Tesseract 5.5.0). F1: PDF-y tekstowe 1,0000, skany w Dockerze 0,9952, łącznie 0,9994
(szczegóły w sekcji „OCR: wersja silnika a wynik” poniżej).
LLM jest **tylko fallbackiem dla pól niejednoznacznych**, gdy reguły nie dadzą wyniku. Tryb `--no-llm` oraz brak
klucza działają bez żadnego połączenia z API, a wynik na zbiorze treningowym jest identyczny.

**Tesseract to OCR, nie model LLM.** Zamienia obraz strony skanu na tekst (lokalnie, open source, Apache 2.0);
nie rozumie treści i nie wypełnia pól. W runtime nie ma innych modeli: ani komercyjnych/zamkniętych (Gemini, Claude),
ani lokalnych LLM-ów.

### Rola LLM-a w pipeline
- **LLM tylko jako fallback dla pól niejednoznacznych:** `czynnosc_powod_id` (1–12) z pierwszego zarzutu, tytuł zamówienia,
  pola oznaczone `llm: true` w YAML oraz normalizacja odmienionych imion i nazwisk do mianownika.
- **Pola deterministyczne** (NIP, KRS, REGON, daty ISO, kwoty, rachunki, numery) wyciąga regex i mają bezwzględne
  pierwszeństwo przed wynikiem LLM-a.
- **Walidacja odpowiedzi LLM:** id spoza słownika 1–12, puste napisy i „null” → `null`; tytuł przyjmowany tylko,
  gdy jego słowa występują w dokumencie; nazwa osoby tylko jako co najmniej dwa człony.
- **Brak klucza, `--no-llm` lub błąd API** → tryb regex + heurystyka, pola niewyciągalne to `null` (nigdy zgadywane wartości).

### Dlaczego nie `ministral-8b-2410`
`ministral-8b-2410` (8 019 808 256 parametrów, licencja MRL-0.1) jest wycofany z API Mistral: nie występuje na liście
`/v1/models`, a zapytanie z tym identyfikatorem jest po cichu obsługiwane przez `ministral-8b-latest` = `ministral-8b-2512`
(Ministral 3 8B, ok. 8,4 mld parametrów LM + ok. 0,4 mld enkodera obrazu, czyli powyżej 8B). Stąd wybór `ministral-3b-2512`.

### Weryfikacja wyboru modelu (21.11.2026)
- Lista `/v1/models` zawiera `ministral-3b-2512` (opis „Ministral 3 3B Instruct”, kontekst 131072).
- Test normalizacji nazwisk (6 odmienionych form) dał dla 3B takie same, poprawne wyniki jak dla 8B-2512.
- Jakość 3B w fallbacku `czynnosc_powod_id` / tytuł zamówienia nie była mierzona, ponieważ na zbiorze treningowym fallback nie jest wywoływany.

## Elastyczność — konfiguracja bez zmiany kodu

Cała wiedza o typach dokumentów jest w YAML (`config/document_types.yaml` + `config/custom/*.yaml`). Konfiguracja obejmuje:

| Obszar | Gdzie w YAML | Co ustawia |
|---|---|---|
| **(a) reguły rozpoznawania** | `weighted_keywords`, `keywords`, `negative_keywords`, `classify_min_score` | Klasyfikacja po treści (nie po nazwie pliku). Sygnały negatywne odejmują punkty, np. orzeczenia KIO trafiają do `{}` |
| **(b) walidacja** | `fields` / drzewo `extract`, typ reguły, słowniki | Dozwolona struktura JSON-a (pole spoza definicji = błąd `INVALID`) i format pól wynikający z reguły: `party_id` → NIP/KRS 10 cyfr, REGON 9/14, PESEL 11; `header_date` → data ISO; `choice` → tylko wartości z listy; `regex` → wzorzec; opcjonalny klucz `validate` → regex, który musi spełnić wartość z LLM-a. Słownik `czynnosc_powod` ogranicza id do 1–12. Wartość z LLM-a (`llm: true`) jest przyjmowana tylko po tej walidacji i gdy występuje w tekście dokumentu |
| **(c) mapowanie pól** | `extract` (zagnieżdżenie kluczy = struktura wyniku), `party`, `labels`, `options`, `czynnosc_keywords` | Która reguła i które etykiety wypełniają które pole wynikowego JSON-a; mapa fraz z pierwszego zarzutu → `czynnosc_powod_id` |
| **(d) nowe typy dokumentów** | nowa sekcja `classes.<id>` albo plik `config/custom/<id>.yaml` | Nowa klasa działa od razu w CLI i GUI. Budulce reguł: `header_date`, `header_place`, `party_name`, `party_id`, `numer_postepowania`, `regex`, `choice`, `signers`, `const` |

**Dowód:** 4. typ, zgłoszenie przystąpienia do postępowania odwoławczego (art. 524 Pzp), jest dodany wyłącznie
konfiguracją. To sekcja `przystapienie` w YAML, a w kodzie nie ma ani jednej linii specyficznej dla tego typu.
Wynik: F1 = 1,0000 na 3 przykładach z `examples/przystapienie/` (25 pól), 0 wywołań LLM.

**Kreator w GUI** (zakładka „Nowy typ dokumentu”) tworzy taki typ bez pisania YAML-a. Formularz zbiera identyfikator,
słowa kluczowe z wagami i pola JSON-a z regułą z listy budulców. „Testuj na PDF-ie” pokazuje punktację klasyfikatora,
wynikowy JSON i pochodzenie każdego pola. „Zapisz typ” tworzy `config/custom/<id>.yaml`, wczytywany przez
`load_config()` bez restartu i bez zmian w kodzie.

## OCR: wersja silnika a wynik

Skany bez warstwy tekstowej (średnio < 200 znaków na stronę) idą przez Tesseract (`pol`, strona renderowana w 300 dpi).
Wynik skanów zależy od **wersji silnika**, choć reguły, model `pol.traineddata` (sha256 `c4476cdb…`, pakiet
`tesseract-ocr-pol` 4.1.0) i Leptonica 1.84.1 są identyczne:

| Środowisko | Tesseract | Strony skanów z tekstem identycznym jak 5.4.0 | F1 skany (30) | F1 łącznie (210) |
|---|---|:---:|:---:|:---:|
| Windows (rozwój reguł) | 5.4.0 | 79 / 79 | 1,0000 | 1,0000 |
| **Docker z RUN.md (środowisko jury)** | **5.5.0 (Debian)** | 8 / 79 | **0,9952** | **0,9994** |
| Ubuntu 24.04, apt (test) | 5.3.4 | — | 0,9952 | 0,9994 |

Przed dwiema normalizacjami wariantów OCR (patrz README) Docker dawał 0,9871 na skanach i 0,9983 łącznie. Normalizacje
to `numer_ted` z serią „S” odczytaną jako „8” oraz numer „1.” odczytany jako „4” na liście pod „wnoszę odwołanie wobec:”;
na 5.4.0 nie zmieniają żadnego pliku wynikowego. Pozostały błąd (skan odwołania 0005: 5.5.0 i 5.3.4 nie odczytują linii
„Zamawiający:”) wymagałby innego sposobu wyznaczania bloku strony, a nie normalizacji, więc go nie ruszamy. Obraz zostaje
przy Tesseracie z Debiana. Raport wsadowy zapisuje wersję silnika (`silnik_ocr`), a `evaluate.py` dzieli F1 na PDF-y
tekstowe i skany.

## Formaty pól wynikowych (wybrane zasady UZP)

- **Rachunki bankowe** (UZP, Discord 21:17 i 23:00): numer jak w dokumencie, usunięte są wyłącznie białe znaki,
  prefiks kraju zostaje. `PL05 4706 8028 …` → `PL0547068028…`, numer bez prefiksu → same cyfry, inny kod kraju IBAN też zostaje.
  Prefiksem są dwie wielkie litery kodu kraju IBAN tuż przed numerem, w tej samej linii, niebędące końcówką słowa
  (`GDAŃSK⏎60 1010…` nie daje prefiksu `SK`). Wzorcowe JSON-y UZP dla 48 z 60 tekstowych potwierdzeń przelewu nie mają `PL`,
  choć dokument go zawiera (96 pól). To znany błąd wzorców, opisany w [EVAL.md](EVAL.md). F1 wg definicji UZP się nie zmienia
  (podobieństwo 0,963 ≥ 0,80).
- **Nazwa firmy przy JDG** (UZP 21:17): dokładnie jak w dokumencie, także gdy różni się od wpisu w CEIDG. Ekstraktor
  nie korzysta z żadnych rejestrów i nie przepisuje nazw. Sprawdzone na 27 polach nazw (m.in. 4 mocodawców JDG
  i 5 odwołujących JDG), w których 0 różnic z wzorcem, a każda nazwa występuje dosłownie w tekście dokumentu.
- **Imiona i nazwiska** w mianowniku (UZP 15:55); brak danych → `null`, nigdy wartość zgadywana.

## Integracja z pipeline'em (tryb wsadowy)

- **Walidacja przed zapisem** (`validation.py`): wynik musi być poprawnym JSON-em (bez NaN/Infinity), zawierać tylko
  pola z definicji klasy (`fields` / `extract` w YAML) i klucze główne, a pola z formatem muszą go spełniać
  (NIP/KRS 10 cyfr, REGON 9/14, PESEL 11, daty RRRR-MM-DD, kwota liczbą, `czynnosc_powod_id` 1–12).
  Walidator niczego nie poprawia — błąd to status `INVALID` w raporcie (plik zostaje do wglądu), JSON niezapisywalny → `{}`.
  Na 439 plikach (wyniki 210 + 16 negatywów, ground truth UZP, przykłady przystąpienia) — 0 fałszywych alarmów.
- **Raport wsadowy**: `<output>_raport.json` (dla automatu) i `<output>_raport.csv` (Excel, `;`, UTF-8 z BOM),
  ścieżkę zmienia `--report`. Dla każdego pliku: wynik, typ, status (`OK` / `ODRZUCONO` / `INVALID` / `ERR`),
  czas w sekundach, OCR, liczba stron, błędy; w podsumowaniu liczba workerów, czas zegarowy całości oraz średni czas
  PDF-a tekstowego i skanu z OCR (czas dokumentu mierzy worker, który go przetwarzał).
- **Równoległość**: `--workers N` procesów roboczych (domyślnie min(2, liczba rdzeni dostępnych dla procesu); `1` = jeden
  proces bez puli). Każdy dokument jest ekstrahowany niezależnie, największe pliki idą pierwsze. Proces główny zapisuje
  JSON-y, waliduje je, pisze raport i robi spójność sprawy w kolejności plików jak przy `--workers 1` (pierwszy dokument
  danego typu w sprawie wygrywa), więc wynik nie zależy od liczby workerów ani od kolejności, w jakiej skończą.
  Worker uruchamia Tesseracta z `OMP_THREAD_LIMIT=1`: Tesseract 5.5.0 z Debiana jest zbudowany z OpenMP i na 2 rdzeniach
  liczył tę samą stronę 16,5 s zamiast 4,7 s, przy identycznym tekście (zmienna ustawiona ręcznie ma pierwszeństwo).
  Gdy proces roboczy padnie (segfault biblioteki PDF, brak pamięci), pliki, które były w nim w toku, idą jeszcze raz,
  każdy we własnym procesie. Plik, który znów go zabija, dostaje `{}` i status `ERR`, reszta batcha przechodzi normalnie.
  Zapis cache OCR jest atomowy (plik tymczasowy + `os.replace`), bo dwa workery mogą jednocześnie OCR-ować ten sam skan.
- **Exit code**: `0` — wszystkie pliki przetworzone (dokument obcy → `{}` to poprawny wynik), `1` — co najmniej jeden
  `ERR` (wyjątek, plik nie-PDF, brak tekstu, skan bez dostępnego OCR) lub `INVALID`, `2` — brak pliku/folderu wejściowego.
  Jeden zły plik nie zatrzymuje batcha — każdy PDF ma swój JSON w `output`.

## Uruchomienie produkcyjne

- **Docker** (`Dockerfile`): Python 3.13 + `tesseract-ocr` + `tesseract-ocr-pol` (build kończy się błędem, jeśli języka `pol`
  brak). `.dockerignore` wyklucza `.env`, dane UZP, wyniki i cache. Katalog aplikacji jest montowany jako `/app`, więc
  `input/`, `output/` i `.env` są czytane z serwera. Jedna komenda: `bash /opt/solution/run.sh` ([RUN.md](RUN.md)).
- **Bez Dockera:** Python 3.12 lub 3.13 + `pip install -r requirements.txt` + Tesseract z `pol`. Gdy Tesseracta brak,
  aplikacja się nie wysypuje: PDF-y tekstowe działają normalnie, skan bez warstwy tekstowej dostaje `{}` i status `ERR`.
- **GUI:** `python app.py --serve --host 0.0.0.0 --port 8000` (domyślnie `127.0.0.1`, czyli tylko lokalnie).
