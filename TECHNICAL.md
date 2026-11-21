# Dokumentacja techniczna — Odwołania2JSON

Opis architektury i użycia modelu LLM. Instrukcja uruchomienia i wyniki: [README.md](README.md), raport F1: [EVAL.md](EVAL.md).

## Architektura (skrót)

PDF → ekstrakcja tekstu (warstwa tekstowa, a dla skanów OCR Tesseract `pol`) → klasyfikacja typu dokumentu
(sygnały słownikowe w `config/document_types.yaml`) → ekstraktor deterministyczny (regex + heurystyka;
nowe typy dokumentów wyłącznie przez sekcję `extract` w YAML) → walidacja formatu i schematu UZP →
walidacja spójności sprawy (`case_consistency.py`) → JSON. Dokumenty nierozpoznane i negatywne dają `{}`.

## Integracja z pipeline'em (tryb wsadowy)

- **Walidacja przed zapisem** (`validation.py`): wynik musi być poprawnym JSON-em (bez NaN/Infinity), zawierać tylko
  pola z definicji klasy (`fields` / `extract` w YAML) i klucze główne, a pola z formatem muszą go spełniać
  (NIP/KRS 10 cyfr, REGON 9/14, PESEL 11, daty RRRR-MM-DD, kwota liczbą, `czynnosc_powod_id` 1–12).
  Walidator niczego nie poprawia — błąd to status `INVALID` w raporcie (plik zostaje do wglądu), JSON niezapisywalny → `{}`.
  Na 439 plikach (wyniki 210 + 16 negatywów, ground truth UZP, przykłady przystąpienia) — 0 fałszywych alarmów.
- **Raport wsadowy**: `<output>_raport.json` (dla automatu) i `<output>_raport.csv` (Excel, `;`, UTF-8 z BOM),
  ścieżkę zmienia `--report`. Dla każdego pliku: wynik, typ, status (`OK` / `ODRZUCONO` / `INVALID` / `ERR`),
  czas w sekundach, OCR, liczba stron, błędy; w podsumowaniu średni czas PDF-a tekstowego i skanu z OCR.
- **Exit code**: `0` — wszystkie pliki przetworzone (dokument obcy → `{}` to poprawny wynik), `1` — co najmniej jeden
  `ERR` (wyjątek, plik nie-PDF, brak tekstu, skan bez dostępnego OCR) lub `INVALID`, `2` — brak pliku/folderu wejściowego.
  Jeden zły plik nie zatrzymuje batcha — każdy PDF ma swój JSON w `output`.

## Model LLM

| Parametr | Wartość |
|---|---|
| Model | **Ministral 3 3B Instruct** (`ministral-3b-2512`), przypięty do wersji |
| Wagi / licencja | open weights, Apache 2.0 (`mistralai/Ministral-3-3B-Instruct-2512`) |
| Rozmiar | 3,4 mld (model językowy) + 0,4 mld (enkoder obrazu) = **3,8 mld parametrów razem** (karta HF) — **≤ 8B → 20 pkt** wg tabeli UZP |
| Dostawca / dostęp | Mistral AI, API z UE (`https://api.mistral.ai/v1`); brak hostingu prywatnego |
| Liczba modeli w runtime | **jeden** — ten sam model obsługuje wszystkie wywołania LLM |
| Konfiguracja | `LLM_API_KEY`, opcjonalnie `LLM_API_BASE`, `LLM_MODEL` (domyślnie `ministral-3b-2512`) |

Modele komercyjne/zamknięte (Gemini, Claude) nie są używane w runtime. Aliasy `*-latest` nie są używane,
bo wskazują na zmieniające się wersje i rozmiary.

### Dlaczego nie `ministral-8b-2410`
`ministral-8b-2410` (8 019 808 256 parametrów, licencja MRL-0.1) jest wycofany z API Mistral: nie występuje na liście
`/v1/models`, a zapytanie z tym identyfikatorem jest po cichu obsługiwane przez `ministral-8b-latest` = `ministral-8b-2512`
(Ministral 3 8B, ok. 8,4 mld parametrów LM + ok. 0,4 mld enkodera obrazu, czyli powyżej 8B). Stąd wybór `ministral-3b-2512`.

### Rola LLM-a w pipeline
- **Silnik główny jest deterministyczny.** Na 210 dokumentach treningowych oraz 3 przykładach `przystapienie`
  uruchomionych przy włączonym LLM i obecnym kluczu API licznik wywołań wyniósł **0**; F1 = 1,0000
  (210 dokumentów, negatywy 16/16, przystąpienie 3/3).
- **LLM tylko jako fallback dla pól niejednoznacznych:** `czynnosc_powod_id` (1–12) z pierwszego zarzutu, tytuł zamówienia,
  pola oznaczone `llm: true` w YAML oraz normalizacja odmienionych imion i nazwisk do mianownika.
- **Pola deterministyczne** (NIP, KRS, REGON, daty ISO, kwoty, rachunki, numery) wyciąga regex i mają bezwzględne
  pierwszeństwo przed wynikiem LLM-a.
- **Walidacja odpowiedzi LLM:** id spoza słownika 1–12, puste napisy i „null” → `null`; tytuł przyjmowany tylko,
  gdy jego słowa występują w dokumencie; nazwa osoby tylko jako co najmniej dwa człony.
- **Brak klucza, `--no-llm` lub błąd API** → tryb regex + heurystyka, pola niewyciągalne to `null` (nigdy zgadywane wartości).
- Temperatura 0, prompt systemowy wymusza odpowiedź JSON-em i `null` zamiast zmyślania.

### Weryfikacja wyboru modelu (21.11.2026)
- Lista `/v1/models` zawiera `ministral-3b-2512` (opis „Ministral 3 3B Instruct”, kontekst 131072).
- Test normalizacji nazwisk (6 odmienionych form) dał dla 3B takie same, poprawne wyniki jak dla 8B-2512.
- Jakość 3B w fallbacku `czynnosc_powod_id` / tytuł zamówienia nie była mierzona, ponieważ na zbiorze treningowym fallback nie jest wywoływany.
