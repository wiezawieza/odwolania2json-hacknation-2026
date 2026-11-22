# Odwołania2JSON — Urząd Zamówień Publicznych / HackNation 2026

**Od dokumentów do danych. Precyzyjna, deterministyczna i odporna transformacja spraw odwoławczych KIO do kanonicznego modelu JSON.**

Projekt realizuje zadanie automatycznej analizy i ekstrakcji informacji z akt spraw Krajowej Izby Odwoławczej (KIO) dostarczonych w formacie PDF (odwołanie, pełnomocnictwo, potwierdzenie przelewu bankowego) do plików JSON ściśle zgodnych ze schematami Urzędu Zamówień Publicznych (`oczekiwane-json`).

---

## 🏆 Wyniki ewaluacji F1 (210 dokumentów: 60 spraw treningowych + 30 skanów UZP) — w środowisku jury (Docker)

Wynik mierzony oficjalnym skryptem `evaluate.py` zgodnie z definicją metryki UZP:
- **TP (True Positive):** wartość zgodna z ground truth (dla długich stringów: podobieństwo `difflib.SequenceMatcher >= 0.80`).
- **TN (True Negative):** wartość `null` w obu źródłach.
- **FP (False Positive):** wartość tylko w predykcji lub niezgodna z referencją.
- **FN (False Negative):** brak wartości w predykcji, gdy występuje w referencji.
- **Wskaźnik F1:** \( F1 = \frac{2 \cdot TP}{2 \cdot TP + FP + FN} \)

Wynik w środowisku, w którym uruchamia nas jury: obraz Docker z [RUN.md](RUN.md), Linux, Tesseract 5.5.0 z Debiana. Szczegółowy raport jest w [EVAL.md](EVAL.md).

| Zbiór danych | Liczba dok. | TP | TN | FP | FN | F1 |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **PDF-y tekstowe (60 spraw: odwołanie, pełnomocnictwo, przelew)** | 180 | 2032 | 284 | 0 | 0 | **1,0000** |
| **Skany (OCR), Docker / Tesseract 5.5.0** | 30 | 308 | 78 | 2 | 1 | **0,9952** |
| **ŁĄCZNIE, Docker / Tesseract 5.5.0** | **210** | **2340** | **362** | **2** | **1** | **0,9994** |
| *dla porównania: skany na Windows / Tesseract 5.4.0* | *30* | *309* | *79* | *0* | *0* | *1,0000* |

**Skąd różnica na skanach: wersja silnika OCR, nie ekstrakcja.** Reguły, model języka `pol.traineddata` (sha256 `c4476cdb…`) i Leptonica 1.84.1 są w obu środowiskach identyczne. Tesseract 5.5.0 (Docker) i 5.4.0 (Windows) dają jednak identyczny tekst tylko na 8 z 79 stron skanów, bo różnią się drobnymi znakami. Wcześniej, gdy reguły znały tylko warianty z 5.4.0, wynik w Dockerze wynosił: skany 0,9871, łącznie 0,9983 (5 pól w 3 skanach). Dodaliśmy dwie małe normalizacje wariantów OCR, bez zmiany Tesseracta. Na Tesseracie 5.4.0 nie zmieniają żadnego wyniku:
- skan odwołania 0004, `czynnosc_powod_id` i `czynnosc_opis`: numer „1.” na liście pod „wnoszę odwołanie wobec:” odczytany jako „4” (oczekiwane 9 „Decyzja o unieważnieniu postępowania”, było 1). **Naprawione.**
- skan odwołania 0007, `numer_ted`: seria „S” numeru Dz.U. UE odczytana jako „8” (`2026/85` zamiast `2026/S5`; oczekiwane `706707-2026`, było `null`). **Naprawione.**
- skan odwołania 0005, `Zamawiajacy[0].nazwa` (oczekiwane „Regionalne Centrum Zdrowia Publicznego w Dębnicy Górnej”, otrzymane „reprezentowany przez pełnomocnika:”) i `Zamawiajacy[0].regon` (oczekiwane `null`, otrzymane `999749508`, czyli REGON odwołującego). Tesseract 5.5.0 w ogóle nie odczytuje tu linii „Zamawiający:”, więc blok zamawiającego nie zostaje znaleziony. W tekście OCR brakuje etykiety, więc normalizacja nic tu nie da. **Zostaje** (2 FP + 1 FN).

Sprawdziliśmy też Tesseract 5.3.4 z Ubuntu 24.04: wynik identyczny jak na 5.5.0 (skany 0,9952), więc obraz zostaje przy 5.5.0 z Debiana. Pełna lista błędów i podział tekst/skan: [EVAL.md](EVAL.md).

### Wyniki per typ dokumentu (Docker / Tesseract 5.5.0, 210 dokumentów):
- **Odwołanie (70 dokumentów):** F1 = **0,9989** (TP=1390, TN=168, FP=2, FN=1); wszystkie 3 błędy to skan 0005 opisany wyżej
- **Pełnomocnictwo (70 dokumentów):** F1 = **1,0000** (TP=536, TN=188, FP=0, FN=0)
- **Przelew bankowy (70 dokumentów):** F1 = **1,0000** (TP=414, TN=6, FP=0, FN=0)
- **Dokumenty negatywne (16 PDF-ów bez ground truth, w tym postanowienie KIO):** 16/16 → pusty JSON `{}` (raportowane osobno przez `evaluate.py`).
- **Zgłoszenie przystąpienia (4. typ, tylko konfiguracja):** F1 = **1,0000** (3 przykłady, 25 pól).

> **Rachunki bankowe z prefiksem `PL` — znany błąd wzorców UZP.** Zgodnie z odpowiedziami UZP (Discord 21:17 i 23:00) numer rachunku zapisujemy dokładnie jak w dokumencie, usuwając tylko białe znaki: `PL05 4706 8028 …` → `PL0547068028…` (bez prefiksu w dokumencie → same cyfry). W 48 z 60 tekstowych potwierdzeń przelewu dokument zawiera `PL`, a wzorcowe JSON-y UZP go nie mają (96 pól `z_rachunku` / `na_rachunek`). To znany błąd wzorców, więc poprawki nie cofamy. Tam, gdzie wzorzec UZP ma `PL` (skany 0001 i 0006), wynik zgadza się co do znaku. Prefiks nie zmienia F1 wg definicji UZP, bo numer z prefiksem ma podobieństwo 0,963 ≥ 0,80 do wzorca bez prefiksu. Przy ścisłym porównaniu te 96 pól różniłoby się wyłącznie brakującym `PL` we wzorcu. Rozbicie liczy `evaluate.py`, wynik jest w [EVAL.md](EVAL.md).

> Wynik jest w pełni deterministyczny: na zbiorze treningowym LLM nie jest wywoływany ani razu (reguły wypełniają wszystkie pola; sprawdzone uruchomieniem z niedostępnym endpointem API).

> **Zasada UZP (15:55) — Mianownik (casus nominativus):** Wszystkie pola `imie_nazwisko` (pełnomocnicy, mocodawca, podpisujący) są normalizowane do formy mianownikowej. Priorytet ma mianownik z dokumentu (pieczęć, blok podpisu pod kropkami `.....`, formuła przyjęcia pełnomocnictwa, lista radców). Formy odmienione podlegają normalizacji przez LLM (Ministral 3 3B, Mistral API z UE) z walidacją regułową; brak możliwości sprowadzenia do mianownika skutkuje zwróceniem `null` (bezwzględny zakaz kopiowania form deklinacyjnych).

> **Brak overfittingu:** Rozwiązanie przeszło test odporności na zmodyfikowanych, syntetycznych danych (zmienione nazwy podmiotów, przesunięte daty, nowe numery spraw, BZP i TED), zachowując wynik **F1 = 1,0000**. W kodzie nie ma żadnych zahardkodowanych wartości ze zbioru treningowego.

---

## 🚀 Szybki start i uruchomienie

### Uruchomienie na VM ewaluacyjnej — jedna komenda

```bash
bash /opt/solution/run.sh
```

Przetwarza `/opt/solution/input` → `/opt/solution/output` w Dockerze (Python 3.13 + Tesseract `pol` w obrazie; na VM jest Python 3.12 i nie ma Tesseracta). Komenda działa bez interakcji i kończy się sama. Przygotowanie VM, klucz API, GUI i wariant bez Dockera opisuje [RUN.md](RUN.md).

- **Klucz API Mistral** trafia wyłącznie do `/opt/solution/.env` **na serwerze**, nigdy do repozytorium (`.env` jest w `.gitignore` i `.dockerignore`).
- **Bez klucza** aplikacja działa w pełni w trybie deterministycznym. Na zbiorze treningowym wynik jest identyczny, bo LLM nie jest wywoływany ani razu.

### Wymagania
- **Python 3.13+** (wymóg UZP; obraz Docker ma 3.13). Kod jest sprawdzony także na **Pythonie 3.12**, wersji z VM ewaluacyjnej.
- Tesseract OCR z językiem polskim (`tesseract-ocr-pol`) dla skanów bez warstwy tekstowej (w obrazie Docker jest). Bez Tesseracta aplikacja się nie wysypuje: PDF-y tekstowe działają normalnie, a skan dostaje `{}` i status `ERR` w raporcie.

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
*Aplikacja działa w pełni autonomicznie również bez podanego klucza (fallback na reguły deterministyczne).* Na VM ewaluacyjnej plik `.env` z kluczem tworzymy tylko na serwerze (`/opt/solution/.env`, patrz [RUN.md](RUN.md)). Prawdziwy klucz nigdy nie trafia do repozytorium.

### Uruchomienie CLI (przetwarzanie wsadowe)

Aplikacja przetwarza katalog wejściowy `input` i zapisuje pliki JSON o analogicznych nazwach w katalogu `output`:

```bash
python app.py input output
```

Tryb bez LLM (czysty regex + heurystyka, zero wywołań API — także bez normalizacji nazwisk przez LLM):
```bash
python app.py input output --no-llm
```

Batch działa równolegle: domyślnie 2 procesy robocze (tyle, ile vCPU ma VM ewaluacyjna), każdy dokument niezależnie. Wynik jest bajt w bajt taki sam jak w jednym procesie, który można wymusić (albo podać więcej workerów na mocniejszej maszynie):
```bash
python app.py input output --workers 1
```

**Integracja z pipeline'em:** każdy JSON jest walidowany przed zapisem (`validation.py`), a batch zapisuje raport `output_raport.json` + `output_raport.csv` (typ, status `OK` / `ODRZUCONO` / `INVALID` / `ERR`, czas każdego dokumentu, OCR, strony, błędy; ścieżka: `--report`). Exit code: `0` — wszystko przetworzone, `1` — co najmniej jeden błąd przetwarzania lub walidacji, `2` — brak wejścia. Szczegóły: [TECHNICAL.md](TECHNICAL.md).

### Zmierzony czas przetwarzania

**Na 2 rdzeniach, tak jak na VM, 150 samych skanów zajmuje ok. 18 min przy limicie 60 min. Przed zmianą było to ok. 78 min.** Pomogły dwie zmiany w trybie wsadowym. Żadna nie zmienia wyniku:

1. **Tesseract z jednym wątkiem w każdym workerze.** Tesseract 5.5.0 z Debiana (obraz Docker) jest zbudowany z OpenMP i domyślnie dzieli każdą stronę na wątki. Na 2 rdzeniach to nie przyspiesza, tylko spowalnia. Ta sama strona odwołania (2550×3300 px, sam Tesseract) trwa 16,5 s z OpenMP i 4,7 s z jednym wątkiem (`OMP_THREAD_LIMIT=1`), a tekst wychodzi identyczny co do znaku. Worker ustawia `OMP_THREAD_LIMIT=1`, chyba że zmienna jest już ustawiona. Tesseract 5.4.0 na Windows (UB Mannheim) jest zbudowany bez OpenMP, więc tam ta zmiana nic nie robi.
2. **Równoległość.** 2 procesy robocze (`--workers N`, domyślnie min(2, liczba rdzeni)), każdy dokument osobno, największe pliki najpierw. Zapis JSON-ów, walidację, raport i spójność sprawy robi proces główny, w tej samej kolejności plików co przy jednym procesie.

**Pomiar na 2 rdzeniach.** Obraz Docker z RUN.md był przypięty do 2 fizycznych rdzeni laptopa Intel Core i7-6700HQ z 2016 r. (`--cpuset-cpus=0,2` w WSL2). W tym czasie nic innego nie działało, a cache OCR był czyszczony przed każdym przebiegiem. Próbka: 12 skanów, po 4 odwołania, pełnomocnictwa i potwierdzenia przelewu (numery 0002, 0004, 0006, 0008), razem 28 stron. Czas pochodzi z raportu wsadowego (`czas_lacznie_s`), bez ok. 1 s startu kontenera:

| Tryb (12 skanów, 28 stron) | Czas | Na skan | Na stronę | 150 samych skanów |
|---|:---:|:---:|:---:|:---:|
| 1 proces, Tesseract z OpenMP (przed zmianą) | 375,4 s | 31,3 s | 13,4 s | 78 min |
| 1 proces, Tesseract z 1 wątkiem | 159,4 s | 13,3 s | 5,7 s | 33 min |
| **2 workery, Tesseract z 1 wątkiem (domyślnie)** | **84,9 s** | **7,1 s** | **3,0 s** | **17,7 min** |

Powtórzony przebieg z 2 workerami trwał 85,1 s. Wcześniejszy pomiar sprzed zmiany (403 s na 12 skanach, 33,6 s na skan) zgadza się z pierwszym wierszem tabeli. Jeden wątek Tesseracta przyspiesza przetwarzanie 2,4 razy, a 2 workery dokładają kolejne 1,9 razy. Cały katalog `dane` (226 PDF-ów, w tym 33 przez OCR, razem 79 stron) w trybie domyślnym na tych samych 2 rdzeniach: 218 s.

**Czy 150 dokumentów zmieści się w 60 minutach, także gdy wszystkie są skanami?**
- **Na 2 rdzeniach — tak:** 150 × 7,1 s ≈ **18 min**, ponad 3 razy poniżej limitu.
- **Gdyby 2 vCPU VM miały moc tylko jednego rdzenia — też tak.** Tak liczy jeden proces z jednym wątkiem: 150 × 13,3 s ≈ **33 min**. Taki przypadek zachodzi, gdy oba vCPU to wątki HT jednego rdzenia fizycznego. Wprost go nie zmierzyliśmy. W WSL2 przypięcie do pary wątków jednego rdzenia według `lscpu` (`--cpuset-cpus=0,1`) dało ten sam czas co dwa rdzenie (82,1 s), więc WSL2 nie odwzorowuje tu prawdziwej topologii.
- **Typowy zestaw (PDF-y tekstowe i skany) — z dużym zapasem.** PDF tekstowy to ok. 0,01 s. Przy proporcji jak w danych treningowych (226 PDF-ów w 218 s) 150 dokumentów zajmuje ok. **2,5 min**.
- Wszystkie liczby pochodzą z laptopa z 2016 r. Rdzenie VM mogą być szybsze albo wolniejsze. Zapas wynosi ponad 3 razy przy 2 rdzeniach i ok. 1,8 razy przy mocy jednego rdzenia.

**Wynik nie zależy od liczby workerów.** Sprawdziliśmy to bajt po bajcie, z OCR od zera:
- Docker / Tesseract 5.5.0 (2 workery, 2 rdzenie): 226 z 226 JSON-ów (210 z wzorcem i 16 negatywów) jest identycznych z przebiegiem jednoprocesowym sprzed zmiany. `evaluate.py` daje bez zmian F1 0,9994 (PDF-y tekstowe 1,0000, skany 0,9952) i negatywy 16/16. Wyniki 12 skanów z każdego z 5 przebiegów pomiarowych (1 proces i 2 workery) też są identyczne. Przystąpienie: 3 z 3 identyczne z `--workers 1`, F1 1,0000.
- Windows / Tesseract 5.4.0 (2 workery, procesy startowane przez `spawn`): 226 z 226 identycznych z wynikami sprzed zmiany, F1 1,0000, negatywy 16/16, przystąpienie 3 z 3 (F1 1,0000).

Faktyczny czas na VM pokaże próba generalna z RUN.md (30 skanów i jedna sprawa tekstowa). Czas z linii „Gotowe” podzielony przez 30 i pomnożony przez 150 to prognoza dla 150 samych skanów.

**Laptop, Windows, 8 wątków.** Cały katalog `dane` (226 PDF-ów, OCR od zera) zajmuje 180 s z 2 workerami, a w jednym procesie zajmował 377 s. Poniżej pomiar jednoprocesowy z 22.11.2026 dla 210 dokumentów treningowych (180 PDF-ów tekstowych i 30 skanów): łącznie 320 s. Warunki: cache OCR wyczyszczony (każdy skan OCR-owany od zera), laptop Intel Core i7-6700HQ (4 rdzenie / 8 wątków, 2016), 16 GB RAM, Windows, Tesseract 5.4 `pol`. W tym przebiegu F1 = 1,0000, negatywy 16/16, 0 wywołań LLM. Dane pochodzą z raportu wsadowego (`czas_s` każdego dokumentu):

| Rodzaj dokumentu (210 z wzorcem UZP) | Liczba | Łącznie | Średnio | Mediana | 90. percentyl | Maks. |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| PDF tekstowy | 180 | 3,4 s | **0,019 s** | 0,011 s | 0,06 s | 0,35 s |
| Skan (OCR Tesseract, 72 strony) | 30 | 316,6 s | **10,6 s** | 2,8 s | 30,7 s | 36,9 s |
| **Razem** | **210** | **320,0 s** | 1,5 s | 0,012 s | 2,4 s | 36,9 s |

Czas skanu zależy od liczby stron: ok. 4,4 s na stronę, średnio 2,4 strony na skan. Wcześniejszy przebieg na tym samym laptopie dał 8,9 s na skan (rozrzut między przebiegami). Ponowne przetworzenie tego samego skanu korzysta z cache OCR (`.cache_ocr/`, klucz: treść pliku) i trwa ok. 0,03 s.

### Uruchomienie interfejsu webowego (GUI)

```bash
python app.py --serve --port 8000
```
Interfejs dostępny jest pod adresem: `http://localhost:8000` (bez zewnętrznych zależności, Python 3.12 / 3.13+).

**GUI na VM ewaluacyjnej:** port 8000 jest na VM otwarty, więc GUI może tam działać pod `http://<adres-VM>:8000`. Uruchamia się go w Dockerze z `--host 0.0.0.0` (komenda w [RUN.md](RUN.md#gui-na-vm-opcjonalnie-port-8000)); domyślnie GUI nasłuchuje tylko na `127.0.0.1`. Do zgłoszenia wystarczy prezentacja PDF na HackTribe, demo wideo nie jest potrzebne.

Zakładki GUI:
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
     - Numery rachunków: 26 cyfr, zapis jak w dokumencie bez białych znaków, z prefiksem kraju (`PL…`), jeśli dokument go podaje (UZP 21:17, 23:00).
     - Identyfikatory postępowań: numery referencyjne spraw, ogłoszenia BZP i TED (kanoniczny format `XXXXXX-YYYY`).
     - Kwoty i daty transakcji (eliminacja mylenia daty operacji z datą generowania wyciągu).
     - Konsorcja i reprezentacja: prawidłowe rozdzielanie członków konsorcjum (Lider vs Partner) oraz podpisujących.
     - Nazwa firmy przy JDG: dokładnie jak w dokumencie, także gdy różni się od CEIDG (UZP 21:17). Ekstraktor nie korzysta z rejestrów i nie przepisuje nazw.
4. **Model LLM (wyłącznie Open Source / dostawca z UE):** jeden model, Ministral 3 3B Instruct 2512, używany tylko jako fallback. Pełna specyfikacja jest w sekcji [Model LLM — pełna specyfikacja](#-model-llm--pełna-specyfikacja) poniżej.
   - Kolejność w runtime: 1) reguły deterministyczne; 2) **LLM tylko jako fallback dla pól niejednoznacznych** (np. `czynnosc_powod_id` 1–12 z pierwszego zarzutu, tytuł zamówienia, odmienione nazwisko → mianownik); 3) brak klucza / błąd API → `null` bez zgadywania.
   - Odpowiedź LLM-a jest walidowana: id spoza słownika 1–12, puste napisy i „null” → `null`; tytuł przyjmowany tylko, gdy jego słowa występują w dokumencie.
5. **Moduł spójności sprawy (`case_consistency.py`):**
   - Zgodnie z zasadą *„Zrozum sprawę. Nie tylko dokument.”* po przetworzeniu kompletu dokumentów sprawy następuje walidacja i uzupełnianie braków krzyżowych (np. weryfikacja NIP odwołującego vs mocodawcy, numeru postępowania w tytule przelewu), a kontrole OK / UWAGA (w tym kwota przelewu vs wpis od odwołania) są pokazywane w GUI i wypisywane w CLI.
   - **Pochodzenie wartości (`provenance.py`)** — dla GUI: metoda, strona i fragment tekstu źródłowego każdego pola (na 210 dokumentach treningowych lokalizacja w tekście dla 99,8% niepustych pól; pozostałe to pola wyliczane, np. `przez-pelnomocnika = 0`, opisane notą).
6. **Ścisła zgodność ze schematami UZP:**
   - Wygenerowany JSON jest ściśle dopasowany do schematów `odwolanie.json`, `pelnomocnictwo.json`, `przelew.json` (właściwe typy pól, brak cytowań w JSON, brak dodatkowych nieudokumentowanych kluczy).

---

## 🧠 Model LLM — pełna specyfikacja

| Parametr | Wartość |
|---|---|
| Liczba modeli | **jeden** model do wszystkich wywołań LLM-a |
| Model | **Ministral 3 3B Instruct 2512** (`ministral-3b-2512`, przypięty do wersji, bez aliasów `*-latest`) |
| Parametry | **3,4 mld** (model językowy) + **0,4 mld** (enkoder obrazu) = **3,8 mld razem** |
| Architektura | **dense** (gęsta), nie MoE — 3,8 mld to pełny rozmiar, bez podziału na parametry „aktywne” |
| Licencja | **Apache 2.0**, otwarte wagi (`mistralai/Ministral-3-3B-Instruct-2512` na Hugging Face) |
| Dostawca | **Mistral AI**, serwery w UE (Polityka Prywatności: przetwarzanie w UE/EOG) |
| Dostęp | **bezpośrednio API Mistral** (`api.mistral.ai`), bez OpenRoutera i innych pośredników, bez hostingu prywatnego |

- **Silnik główny jest deterministyczny** (regex + heurystyka + YAML): na 210 dokumentach treningowych (oraz 16 negatywach i 3 przykładach przystąpienia) wykonano **0 wywołań LLM**. F1 w Dockerze: PDF-y tekstowe 1,0000, łącznie 0,9994 (różnica tylko na skanach, z powodu wersji silnika OCR).
- **LLM to tylko fallback dla pól niejednoznacznych**, gdy reguły nie dadzą wyniku. Każda odpowiedź jest walidowana, a brak pewności daje `null`.
- **Tryb `--no-llm` działa bez klucza API** i bez połączenia z internetem. Bez pliku `.env` aplikacja przechodzi w ten tryb sama.
- **Tesseract to OCR, nie model LLM**: lokalny, open source (Apache 2.0) silnik zamiany obrazu skanu na tekst. Nie interpretuje treści.
- Modele komercyjne/zamknięte (Gemini, Claude) nie są używane w runtime. Wcześniej rozważany `ministral-8b-2410` jest wycofany z API (po cichu podmieniany na model ok. 8,8 mld), dlatego z niego zrezygnowano. Szczegóły: [TECHNICAL.md](TECHNICAL.md#model-llm--pełna-specyfikacja).

## 🧩 Elastyczność — nowe typy dokumentów bez zmiany kodu

Konfiguracja YAML (`config/document_types.yaml`, `config/custom/*.yaml`) obejmuje: **(a)** reguły rozpoznawania (słowa kluczowe z wagami, sygnały negatywne, próg), **(b)** walidację (dozwolona struktura JSON-a, format pól z typu reguły, słowniki), **(c)** mapowanie pól (drzewo `extract`: ścieżka JSON → reguła i etykiety) i **(d)** nowe typy dokumentów. Wszystko działa bez zmiany kodu.

- **Dowód:** 4. typ (zgłoszenie przystąpienia, art. 524 Pzp) jest dodany wyłącznie sekcją YAML i daje F1 = 1,0000 na `examples/przystapienie/`.
- **Kreator w GUI** (zakładka „Nowy typ dokumentu”) buduje typ z formularza, testuje go na PDF-ie i zapisuje do `config/custom/<id>.yaml`.

Szczegóły: [TECHNICAL.md](TECHNICAL.md#elastyczność--konfiguracja-bez-zmiany-kodu).

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
├── input/                      # Dokumenty wejściowe (PDF, także w podfolderach spraw)
├── output/                     # Wynikowe JSON-y (ta sama nazwa i struktura co w input/)
├── EVAL.md                     # Raport z wynikami ewaluacji F1
├── RUN.md                      # Uruchomienie na VM ewaluacyjnej: jedna komenda, przygotowanie, GUI
├── run.sh                      # Jedna komenda (Docker): input/ -> output/
├── start.bat                   # Uruchomienie na Windows (Python)
├── requirements.txt            # Zależności open-source (Python 3.13+, sprawdzone też na 3.12)
├── Dockerfile                  # Obraz: Python 3.13 + Tesseract OCR (pol)
├── .dockerignore               # .env, dane UZP i wyniki nigdy nie trafiają do obrazu
├── docker-compose.yml          # Alternatywa: batch i GUI przez docker compose
├── .env.example                # Przykładowy plik zmiennych środowiskowych (prawdziwy .env tylko na serwerze)
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
Raport zostanie wyświetlony w konsoli oraz zaktualizowany w pliku `EVAL.md`. Jeśli obok predykcji leży raport wsadowy (`output_raport.json`), `evaluate.py` dzieli F1 na PDF-y tekstowe i skany i podaje wersję silnika OCR.

`EVAL.md` w repozytorium pochodzi z przebiegu w obrazie Docker, czyli w środowisku jury:
```bash
docker run --rm -v "$PWD":/app odwolania2json python app.py hacknation-uzp-repo/dane output_docker
python evaluate.py hacknation-uzp-repo/dane output_docker
```
