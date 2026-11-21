"""Klient LLM — TYLKO modele open-source / open-weights hostowane przez firmę z UE.

Kolejność providerów w runtime:
  1. Open-source model po API z dostawcą z UE (Mistral AI,
     https://api.mistral.ai/v1 — model open-weights Ministral 3 3B, przypięty ministral-3b-2512).
     Klucz: LLM_API_KEY lub MISTRAL_API_KEY. Endpoint: LLM_API_BASE.
     Dostawca posiada Politykę Prywatności obejmującą zakres i miejsce przetwarzania
     danych na terenie UE/EOG (zgodnie z wymogami UZP).
  2. Brak LLM / błąd połączenia: pełny deterministyczny fallback regex+heurystyka
     (aplikacja zwraca JSON z nullami tam, gdzie nie da się wyciągnąć pola deterministycznie).

ZERO modeli komercyjnych zamkniętych (Gemini, Claude) oraz brak hostingu prywatnego.
"""

import contextlib
import json
import os
import urllib.request


def _load_dotenv():
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.isfile(env_file):
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip("'\"")
                    if k and k not in os.environ:
                        os.environ[k] = v


_load_dotenv()

DEFAULT_API_BASE = "https://api.mistral.ai/v1"
# Model PRZYPIĘTY do wersji: Ministral 3 3B Instruct 2512 (3,4B LM + 0,4B vision = 3,8B razem,
# Apache 2.0, open weights: mistralai/Ministral-3-3B-Instruct-2512) — kategoria ≤8B
# wg tabeli UZP. NIE używać aliasów "-latest" ani ministral-8b-* (8B-2512 ma ~8,8B,
# a ministral-8b-2410 jest wycofany z API i podmieniany po cichu na 8B-2512).
DEFAULT_MODEL = "ministral-3b-2512"

_DISABLED = 0
_NAME_CACHE = {}


@contextlib.contextmanager
def llm_disabled():
    """Blok bez żadnego wywołania LLM-a (np. wynik czysto deterministyczny do porównania w GUI)."""
    global _DISABLED
    _DISABLED += 1
    try:
        yield
    finally:
        _DISABLED -= 1


def _llm_off():
    return _DISABLED > 0 or os.environ.get("LLM_OFF") == "1"


def _post_json(url, payload, headers, timeout=60):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _chat_via_api(text, prompt, api_key, api_base, model):
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Jesteś ekstraktorem danych z polskich dokumentów prawnych. "
                    "Odpowiadaj WYŁĄCZNIE poprawnym JSON-em, bez komentarzy. "
                    "Jeśli czegoś nie ma w tekście, wpisz null — nigdy nie wymyślaj danych."
                ),
            },
            {
                "role": "user",
                "content": prompt + "\n\n--- TEKST DOKUMENTU ---\n" + text[:12000],
            },
        ],
        "temperature": 0.0,
    }
    data = _post_json(
        api_base.rstrip("/") + "/chat/completions",
        payload,
        {"Authorization": f"Bearer {api_key}"},
    )
    return data["choices"][0]["message"]["content"]


def extract_fields_llm(text, prompt):
    """Zwraca dict z polami wyciągniętymi przez LLM albo None, gdy brak LLM-a.

    Używane WYŁĄCZNIE do pól niejednoznacznych (czynność, tytuł).
    Pola deterministyczne (NIP, KRS, REGON, daty ISO, kwoty, rachunki, numery)
    wyciąga regex i mają bezwzględne pierwszeństwo przed wynikiem LLM-a.
    """
    if _llm_off():
        return None
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("MISTRAL_API_KEY")
    api_base = os.environ.get("LLM_API_BASE", DEFAULT_API_BASE)
    model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    if not api_key:
        return None
    try:
        raw = _chat_via_api(text, prompt, api_key, api_base, model)
    except Exception as exc:  # noqa: BLE001 — fallback deterministyczny
        print(f"[llm] API ({api_base}) niedostępne: {exc}, tryb regex+heurystyka")
        return None
    if not raw:
        return None
    # Wyciągnij JSON z odpowiedzi (model czasem dodaje ```json ... ```).
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None


def normalize_name_nominative_llm(name):
    """Sprowadza imię i nazwisko do mianownika (casus nominativus) przez Mistral API.
    Udane wyniki są cache'owane — ta sama osoba nie generuje kilku wywołań API."""
    if _llm_off():
        return None
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("MISTRAL_API_KEY")
    api_base = os.environ.get("LLM_API_BASE", DEFAULT_API_BASE)
    model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    if not api_key or not name:
        return None
    key = " ".join(name.split())
    if key in _NAME_CACHE:
        return _NAME_CACHE[key]
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Zamień podane odmienione imię i nazwisko na formę podstawową (mianownik, casus nominativus).\n"
                    "Zachowaj kolejność Imię Nazwisko.\n"
                    "Przykłady:\n"
                    "- Jana Kowalskiego -> Jan Kowalski\n"
                    "- Janowi Kowalskiemu -> Jan Kowalski\n"
                    "- Aleksandry Nowak -> Aleksandra Nowak\n"
                    "Odpowiedz TYLKO samym imieniem i nazwiskiem w mianowniku, bez żadnego dodatkowego tekstu ani cudzysłowów."
                ),
            },
            {"role": "user", "content": name.strip()},
        ],
        "temperature": 0.0,
    }
    try:
        data = _post_json(
            api_base.rstrip("/") + "/chat/completions",
            payload,
            {"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        res = data["choices"][0]["message"]["content"].strip().strip("'\"`")
        if res.lower() in ("null", "none", ""):
            return None
        parts = res.split()
        if len(parts) >= 2:
            _NAME_CACHE[key] = " ".join(parts)
            return _NAME_CACHE[key]
        return None
    except Exception as exc:  # noqa: BLE001
        print(f"[llm] Błąd normalizacji nazwiska ({name}): {exc}")
        return None

