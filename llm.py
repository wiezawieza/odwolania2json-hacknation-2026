"""Klient LLM — TYLKO modele open source.

Kolejność providerów (konfigurowalna przez env):
  1. Open-source model po API z dostawcą z UE (domyślnie Mistral API,
     https://api.mistral.ai/v1 — model open weights, np. mistral-small-latest).
     Klucz: LLM_API_KEY lub MISTRAL_API_KEY. Endpoint: LLM_API_BASE.
     UWAGA: przed użyciem innego dostawcy zweryfikuj na jego stronie, że
     serwery znajdują się na terenie UE (wymóg FAQ v1.0 UZP).
  2. Ollama lokalnie (http://localhost:11434), model qwen2.5:7b.
  3. Brak LLM — funkcje zwracają None, a aplikacja działa dalej
     w trybie regex + heurystyka i zwraca JSON z nullami tam, gdzie
     nie da się wyciągnąć pola deterministycznie.

ZERO modeli zamkniętych (Gemini, Claude) w runtime — są dozwolone
wyłącznie jako narzędzia do pisania kodu, nie jako komponent rozwiązania.
"""

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
DEFAULT_MODEL = "mistral-small-latest"
OLLAMA_BASE = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b"


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
            {"role": "system", "content": "Jesteś ekstraktorem danych z polskich dokumentów prawnych. Odpowiadaj WYŁĄCZNIE poprawnym JSON-em, bez komentarzy. Jeśli czegoś nie ma w tekście, wpisz null — nigdy nie wymyślaj danych."},
            {"role": "user", "content": prompt + "\n\n--- TEKST DOKUMENTU ---\n" + text[:12000]},
        ],
        "temperature": 0.0,
    }
    data = _post_json(
        api_base.rstrip("/") + "/chat/completions",
        payload,
        {"Authorization": f"Bearer {api_key}"},
    )
    return data["choices"][0]["message"]["content"]


def _chat_via_ollama(text, prompt, model):
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Jesteś ekstraktorem danych z polskich dokumentów prawnych. Odpowiadaj WYŁĄCZNIE poprawnym JSON-em, bez komentarzy. Jeśli czegoś nie ma w tekście, wpisz null — nigdy nie wymyślaj danych."},
            {"role": "user", "content": prompt + "\n\n--- TEKST DOKUMENTU ---\n" + text[:12000]},
        ],
        "stream": False,
        "options": {"temperature": 0.0},
    }
    data = _post_json(OLLAMA_BASE + "/api/chat", payload, {}, timeout=180)
    return data["message"]["content"]


def extract_fields_llm(text, prompt):
    """Zwraca dict z polami wyciągniętymi przez LLM albo None, gdy brak LLM-a.

    Używane WYŁĄCZNIE do pól niejednoznacznych (czynność, tytuł, nazwy,
    miejscowość, data nagłówka). Pola deterministyczne (NIP, KRS, daty ISO,
    numery) wyciąga regex i mają pierwszeństwo przed wynikiem LLM-a.
    """
    if os.environ.get("LLM_OFF") == "1":
        return None
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("MISTRAL_API_KEY")
    api_base = os.environ.get("LLM_API_BASE", DEFAULT_API_BASE)
    model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    raw = None
    if api_key:
        try:
            raw = _chat_via_api(text, prompt, api_key, api_base, model)
        except Exception as exc:  # noqa: BLE001 — API niedostępne, próbuj Ollamę
            print(f"[llm] API ({api_base}) niedostępne: {exc}, próbuję Ollamę")
    if raw is None:
        try:
            raw = _chat_via_ollama(text, prompt, os.environ.get("OLLAMA_MODEL", OLLAMA_MODEL))
        except Exception as exc:  # noqa: BLE001 — brak Ollamy, tryb regex
            print(f"[llm] Ollama niedostępna: {exc}, tryb regex+heurystyka")
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
