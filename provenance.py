"""Pochodzenie wartości (transparentność ekstrakcji) — wyłącznie dla GUI.

Dla każdego pola wynikowego JSON-a podajemy:
  * metodę: regex / heurystyka / LLM / null (brak danych) / spójność sprawy,
  * stronę PDF-a i fragment tekstu źródłowego, w którym wartość występuje,
  * krótką notę, jak wartość powstała.

Metoda „LLM” = pole, które różni się od wyniku czysto deterministycznego (ten sam
dokument przetworzony z wyłączonym LLM-em) — to fakt, nie deklaracja.
Metadane NIE trafiają do wynikowego JSON-a (CLI i „Pobierz JSON” zwracają czysty wynik).
"""

import re

from extractor import (CZYNNOSC_RULES, EXTRACTORS, GENERIC_RULE_METHOD, MONTHS_PL,
                       deterministic_result, generic_rules)

_MISSING = object()

METHOD_LABELS = {
    "regex": "regex",
    "heurystyka": "heurystyka",
    "llm": "LLM",
    "null": "null — brak danych",
    "spojnosc": "spójność sprawy",
}

# Pola klas wbudowanych wyciągane wzorcem z walidacją formatu; pozostałe to heurystyki
# (etykiety, pozycja w dokumencie, reguły słownikowe).
REGEX_LEAVES = {
    "nip", "krs", "regon", "pesel", "data", "czynnosc_dzien", "data_operacji", "numer_bzp",
    "numer_ted", "numer_postepowania", "mail", "kwota", "z_rachunku", "na_rachunek",
    "numer_transakcji",
}

NOTES = {
    "nip": "10 cyfr po etykiecie „NIP” w bloku strony",
    "krs": "10 cyfr po etykiecie „KRS”",
    "regon": "9 lub 14 cyfr po etykiecie „REGON”",
    "pesel": "11 cyfr po „PESEL” przy osobie",
    "data": "data z nagłówka „Miejscowość, data”",
    "miejscowosc": "miejscowość z nagłówka „Miejscowość, data”",
    "czynnosc_dzien": "data zaskarżonej czynności („w dniu …”)",
    "czynnosc_powod_id": "reguły słownikowe na pierwszym zarzucie → słownik UZP 1–12",
    "czynnosc_opis": "opis ze słownika UZP dla ustalonego id",
    "numer_bzp": "numer ogłoszenia RRRR/BZP…",
    "numer_ted": "numer ogłoszenia w Dz.U. UE, format NNNNNN-RRRR",
    "numer_postepowania": "token po „nr referencyjny / znak sprawy”",
    "tytul": "tytuł z dokumentu (np. w cudzysłowie po „pn.” / pole „Tytuł”)",
    "mail": "adres e-mail w bloku strony",
    "kwota": "kwota z groszami przy etykiecie „Kwota”",
    "z_rachunku": "IBAN (26 cyfr) nadawcy",
    "na_rachunek": "IBAN (26 cyfr) odbiorcy",
    "numer_transakcji": "identyfikator transakcji / numer referencyjny",
    "data_operacji": "data operacji / wykonania przelewu",
    "imie_nazwisko": "imię i nazwisko w mianowniku (blok podpisu, lista pełnomocników)",
    "nazwa": "nazwa podmiotu z bloku strony (przy konsorcjum — lider)",
    "przez-pelnomocnika": "1 — wskazano pełnomocnika, 0 — brak",
    "typ": "osoba_fizyczna przy „Ja, niżej podpisany”, inaczej osoba_prawna",
    "prowadzacy_postepowanie": "oznaczenie zamawiającego prowadzącego wspólne postępowanie",
    "strona": "po której stronie przystępuje wykonawca",
}


def flatten(obj, prefix=""):
    """[(ścieżka, wartość)] w kolejności JSON-a; pusta lista to liść []."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.extend(flatten(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(obj, list):
        if not obj:
            out.append((prefix, []))
        for i, v in enumerate(obj):
            out.extend(flatten(v, f"{prefix}[{i}]"))
    else:
        out.append((prefix, obj))
    return out


# ---------------------------------------------------------------- wyszukiwanie w tekście
def _search(pages, patterns, prefer=None, earliest=False):
    """(strona, start, koniec) trafienia wartości w tekście.

    Domyślnie: pierwszy wzorzec (w kolejności priorytetu), który ma trafienie.
    earliest=True — najwcześniejsze trafienie spośród wszystkich wzorców.
    prefer — etykieta, która musi stać bezpośrednio przed wartością (jak w regexie
    ekstraktora, np. „w dniu” przed datą czynności); wygrywa najwcześniejsze takie trafienie.
    """
    hits = []
    for prio, pat in enumerate(p for p in patterns if p):
        n = 0
        for pi, txt in enumerate(pages):
            for m in re.finditer(pat, txt, re.IGNORECASE):
                if m.end() > m.start():
                    hits.append((pi, m.start(), m.end(), prio))
                    n += 1
                if n >= 50:
                    break
            if n >= 50:
                break
        if hits and not earliest and not prefer:
            break
    if not hits:
        return None
    if prefer:
        pref = [h for h in hits
                if re.search(r"(?:" + prefer + r")\W*$", pages[h[0]][max(0, h[1] - 60):h[1]], re.IGNORECASE)]
        if pref:
            return min(pref, key=lambda h: (h[0], h[1]))[:3]
    if earliest or prefer:
        return min(hits, key=lambda h: (h[0], h[1]))[:3]
    return hits[0][:3]


def _tokens_pattern(s):
    toks = re.findall(r"[^\W\d_]+|\d+|[^\w\s]", str(s))
    return r"\s*".join(re.escape(t) for t in toks) if toks else None


def _words_pattern(s, limit=None):
    words = str(s).split()
    if limit:
        words = words[:limit]
    return r"\s+".join(re.escape(w) for w in words) if words else None


def _digits_pattern(d):
    return r"(?<!\d)" + r"[\s\-]{0,2}".join(d) + r"(?!\d)" if d else None


def _date_patterns(iso):
    y, m, d = iso.split("-")
    di, mi = int(d), int(m)
    months = "|".join(name for name, num in MONTHS_PL.items() if num == mi)
    return [
        rf"(?<!\d)0?{di}\s*[./\-]\s*0?{mi}\s*[./\-]\s*{y}",
        rf"{y}-{m}-{d}",
        rf"(?<!\d)0?{di}\s+(?:{months})\s+{y}",
        rf"(?<!\d)0?{di}\s+0?{mi}\s+{y}",
    ]


def _amount_patterns(v):
    cents_total = int(round(abs(v) * 100))
    ip, cents = divmod(cents_total, 100)
    s, groups = str(ip), []
    while s:
        groups.insert(0, s[-3:])
        s = s[:-3]
    ip_pat = r"[\s.,]?".join(groups)
    return [
        rf"(?<![\d.,]){ip_pat}[,.]{cents:02d}(?!\d)",
        rf"(?<![\d.,]){ip_pat}\s*(?:zł|PLN)",
    ]


def _fragment(page_text, start, end, width=90):
    """Linia z trafieniem (+ kontekst) jako before / match / after."""
    ls = page_text.rfind("\n", 0, start)
    ls = 0 if ls < 0 else ls + 1
    le = page_text.find("\n", end)
    le = len(page_text) if le < 0 else le
    s, e = max(ls, start - width), min(le, end + width)
    if (e - s) - (end - start) < 50:  # krótka linia -> pokaż też trochę sąsiednich
        s, e = max(0, start - 55), min(len(page_text), end + 55)

    def c(x):
        return re.sub(r"\s+", " ", x)

    return {
        "before": ("…" if s > 0 else "") + c(page_text[s:start]).lstrip(),
        "match": c(page_text[start:end]),
        "after": c(page_text[end:e]).rstrip() + ("…" if e < len(page_text) else ""),
    }


# ---------------------------------------------------------------- lokalizacja wartości
def _locate(path, leaf, value, pages, flat, config, rule=None):
    """Zwraca (trafienie | None, nota | None)."""
    if value is None or value == []:
        return None, None
    v = value
    kind = (rule or {}).get("rule")
    if leaf in ("nip", "krs", "regon", "pesel", "z_rachunku", "na_rachunek") or kind == "party_id":
        return _search(pages, [_digits_pattern(re.sub(r"\D", "", str(v)))]), None
    if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        prefer = {"czynnosc_dzien": r"w\s+dniu|pismem\s+z|została\s+(?:przekazana|opublikowana)\s+w\s+dniu",
                  "data_operacji": r"data(?:\s+\w+)?|dnia|w\s+dniu|pln"}.get(leaf)
        return _search(pages, _date_patterns(v), prefer=prefer, earliest=True), None
    if leaf == "kwota" and isinstance(v, (int, float)):
        return _search(pages, _amount_patterns(v)), None
    if leaf == "numer_ted" and isinstance(v, str):
        num, _, year = v.partition("-")
        hit = _search(pages, [_tokens_pattern(v), rf"{year}\s*/\s*[S5]\s*\d*\s*[-–—]?\s*{num}", _digits_pattern(num)])
        return hit, ("w dokumencie format Dz.U. UE (RRRR/S NNN-NNNNNN), zapisano jako NNNNNN-RRRR" if hit else None)
    if leaf == "mail" and isinstance(v, str):
        hit = _search(pages, [re.escape(v)])
        if hit:
            return hit, None
        hit = _search(pages, [re.escape(v.split("@")[0])])
        return hit, ("adres w tekście zniekształcony (OCR) — naprawiono znak @ / domenę" if hit else None)
    if leaf == "imie_nazwisko" and isinstance(v, str):
        hit = _search(pages, [_words_pattern(v)])
        if hit:
            return hit, None
        parts = v.split()
        if len(parts) >= 2:
            hit = _search(pages, [rf"\b{re.escape(parts[0][:3])}\w*\s+{re.escape(parts[-1][:4])}\w*"])
            if hit:
                return hit, "w tekście forma odmieniona — zapisano w mianowniku"
        return None, None
    if leaf in ("czynnosc_powod_id", "czynnosc_opis"):
        cid = flat.get("OdwolanieKIO.czynnosc_powod_id")
        pats = [p for c, ps in CZYNNOSC_RULES if c == cid for p in ps]
        for *keys, c in config.get("czynnosc_keywords", []):
            if c == cid:
                pats.extend(re.escape(k) for k in keys)
        opis = (config.get("czynnosc_powod") or {}).get(cid)
        return _search(pages, pats, earliest=True), f"słownik UZP: {cid} = {opis}"
    if leaf == "przez-pelnomocnika":
        if v == 1:
            sib = path.rsplit(".", 1)[0] + ".pelnomocnik.imie_nazwisko"
            hit, _ = _locate(sib, "imie_nazwisko", flat.get(sib), pages, flat, config)
            return hit, "wyliczone: 1 — w dokumencie wskazano pełnomocnika"
        return None, "wyliczone: 0 — w bloku odwołującego nie wskazano pełnomocnika"
    if leaf == "typ":
        if v == "osoba_fizyczna":
            return _search(pages, [r"niżej\s+podpisan\w*"]), "wyliczone z formuły „Ja, niżej podpisany”"
        sib = path.rsplit(".", 1)[0] + ".nazwa"
        hit, _ = _locate(sib, "nazwa", flat.get(sib), pages, flat, config)
        return hit, "wyliczone: mocodawcą jest podmiot (firma)"
    if leaf == "prowadzacy_postepowanie":
        if v == 1:
            return _search(pages, [r"prowadz\w*\s+postępowani\w*", r"\blider\w*"]), \
                "wyliczone: zamawiający oznaczony jako prowadzący postępowanie"
        return None, "wyliczone: 0 — wspólne postępowanie prowadzi inny zamawiający"
    if kind == "choice":
        for opt_value, pat in rule.get("options", []):
            if opt_value == v:
                return _search(pages, [pat + r"\w*"]), None
    if isinstance(v, bool):
        return None, None
    if isinstance(v, (int, float)):
        return _search(pages, [rf"(?<!\d){re.escape(str(v))}(?!\d)"]), None
    s = str(v)
    if leaf in ("numer_bzp", "numer_postepowania", "numer_transakcji"):
        return _search(pages, [_tokens_pattern(s)]), None
    hit = _search(pages, [_words_pattern(s), _tokens_pattern(s)])
    if hit:
        return hit, None
    hit = _search(pages, [_words_pattern(s, 6)])
    return hit, ("dopasowano początek tekstu (łamanie wierszy w PDF-ie)" if hit else None)


def _null_sought(leaf, rule):
    """Czego szukała reguła — wyjaśnienie dla urzędnika, dlaczego pole jest null."""
    if rule:
        kind = rule.get("rule")
        labels = ", ".join(f"„{x}”" for x in ([rule["label"]] if isinstance(rule.get("label"), str)
                                              else rule.get("label") or []))
        if kind == "party_name":
            return f"nazwy podmiotu w bloku strony od etykiety {labels}"
        if kind == "party_id":
            return f"{str(rule.get('id', '')).upper()} w bloku strony od etykiety {labels}"
        if kind == "regex":
            return f"wzorca {rule.get('pattern')}"
        if kind == "choice":
            return "jednej z fraz: " + ", ".join(f"„{o[0]}”" for o in rule.get("options", []))
        if kind == "signers":
            return "bloku podpisu z imieniem i nazwiskiem"
        if kind == "numer_postepowania":
            return "numeru po etykiecie „nr referencyjny / znak sprawy”"
        if rule.get("hint"):
            return rule["hint"]
    return NOTES.get(leaf)


def build_provenance(result, doc_type, trace, config, filled_from=None):
    """Lista wierszy {path, value, method, method_label, page, fragment, note} dla GUI.

    filled_from: {ścieżka: opis_źródła} — pola uzupełnione przez spójność sprawy.
    """
    if not result:
        return []
    pages = trace.get("pages") or [trace.get("text", "")]
    det = deterministic_result(trace.get("text", ""), config, doc_type)
    det_flat = dict(flatten(det))
    flat = dict(flatten(result))
    rules = {} if doc_type in EXTRACTORS else generic_rules(config, doc_type)
    requested = set(trace.get("llm_requested") or [])
    filled_from = filled_from or {}
    rows = []
    for path, value in flatten(result):
        bare = re.sub(r"\[\d+\]", "", path)
        leaf = bare.rsplit(".", 1)[-1]
        rule = rules.get(bare) or rules.get(bare.rsplit(".", 1)[0])  # np. podpisujacy[0].imie_nazwisko
        if path in filled_from:
            method = "spojnosc"
        elif value is None or value == []:
            method = "null"
        elif det_flat.get(path, _MISSING) != value:
            method = "llm"
        elif rule:
            method = GENERIC_RULE_METHOD.get(rule.get("rule"), "heurystyka")
        else:
            method = "regex" if leaf in REGEX_LEAVES else "heurystyka"
        hit, extra = _locate(path, leaf, value, pages, flat, config, rule)
        if method == "null":
            asked = bare in requested or leaf in requested
            note = ("reguły nie znalazły wartości, LLM nie podał wartości obecnej w tekście → null"
                    if asked else "brak danych w dokumencie → null (nie wymyślamy wartości)")
            sought = _null_sought(leaf, rule)
            if leaf == "imie_nazwisko" and flat.get(path.rsplit(".", 1)[0] + ".typ") == "osoba_prawna":
                note = "mocodawcą jest podmiot (firma), nie osoba fizyczna — imię i nazwisko nie dotyczy → null"
            elif sought:
                note = f"szukano: {sought} — nie znaleziono; {note}"
        elif method == "spojnosc":
            note = f"uzupełnione z innego dokumentu sprawy: {filled_from[path]}"
        elif method == "llm":
            note = "uzupełnione przez LLM (Mistral, UE) — przyjęte po walidacji, że wartość jest w tekście"
            if leaf == "imie_nazwisko":
                note = "sprowadzone do mianownika przez LLM (Mistral, UE)"
        else:
            note = (rule or {}).get("hint") or NOTES.get(leaf, "")
        if extra:
            note = f"{note}; {extra}" if note else extra
        rows.append({
            "path": path,
            "value": value,
            "method": method,
            "method_label": METHOD_LABELS[method],
            "page": hit[0] + 1 if hit else None,
            "fragment": _fragment(pages[hit[0]], hit[1], hit[2]) if hit else None,
            "note": note,
        })
    return rows
