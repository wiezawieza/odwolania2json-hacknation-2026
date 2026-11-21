"""Walidacja wyniku przed zapisem pliku JSON (tryb wsadowy / pipeline).

Sprawdza, bez zmieniania wartości:
  * czy wynik da się zapisać jako poprawny JSON (bez NaN/Infinity, typy JSON),
  * dokument obcy -> dokładnie {},
  * strukturę: tylko pola z definicji klasy (`fields` / `extract` w YAML), obecne klucze główne,
  * format pól, które mają format: NIP/KRS (10 cyfr), REGON (9/14), PESEL (11), daty ISO,
    kwota liczbą, czynnosc_powod_id 1–12.
Zwraca listę błędów (pusta = poprawny).
"""

import datetime
import json
import re

from extractor import EXTRACTORS, generic_rules

_ID_FORMAT = {"nip": r"\d{10}", "krs": r"\d{10}", "regon": r"\d{9}|\d{14}", "pesel": r"\d{11}"}
_DATE_LEAVES = {"data", "czynnosc_dzien", "data_operacji"}


def _flatten(obj, prefix=""):
    out = []
    if isinstance(obj, dict):
        if not obj and prefix:
            out.append((prefix, obj))
        for k, v in obj.items():
            out.extend(_flatten(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(obj, list):
        if not obj:
            out.append((prefix, obj))
        for i, v in enumerate(obj):
            out.extend(_flatten(v, f"{prefix}[{i}]"))
    else:
        out.append((prefix, obj))
    return out


def expected_paths(config, doc_type):
    """Ścieżki liści z definicji klasy, listy jako `[]` (np. Odwolujacy[].nip)."""
    cls = config.get("classes", {}).get(doc_type) or {}
    if doc_type in EXTRACTORS or not cls.get("extract"):
        if cls.get("fields"):
            return set(cls["fields"])
    return set(generic_rules(config, doc_type))


def _is_iso_date(s):
    try:
        datetime.date.fromisoformat(s)
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", s))
    except (TypeError, ValueError):
        return False


def validate_result(result, doc_type, config):
    try:
        json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        return [f"wynik nie jest poprawnym JSON-em: {exc}"]
    if not isinstance(result, dict):
        return ["wynik nie jest obiektem JSON"]
    if doc_type == "nieznany":
        return [] if result == {} else ["dokument obcy musi dać pusty JSON {}"]
    if not result:
        return [f"pusty JSON dla rozpoznanego typu {doc_type}"]

    errors = []
    expected = expected_paths(config, doc_type)
    if expected:
        tops = {re.sub(r"\[\]$", "", p.split(".")[0]) for p in expected}
        for top in sorted(tops - set(result)):
            errors.append(f"brak pola głównego „{top}”")
    for path, value in _flatten(result):
        bare = re.sub(r"\[\d+\]", "[]", path)
        if expected and bare not in expected and not any(
                e.startswith(bare + ".") or e.startswith(bare + "[]") or bare.startswith(e + "[]")
                for e in expected):
            errors.append(f"pole spoza definicji klasy: {path}")
            continue
        if value is None or isinstance(value, (list, dict)):
            continue
        leaf = bare.rsplit(".", 1)[-1]
        if leaf in _ID_FORMAT and not (isinstance(value, str) and re.fullmatch(_ID_FORMAT[leaf], value)):
            errors.append(f"{path}: niepoprawny format {leaf.upper()} ({value!r})")
        elif leaf in _DATE_LEAVES and not (isinstance(value, str) and _is_iso_date(value)):
            errors.append(f"{path}: data nie jest w formacie RRRR-MM-DD ({value!r})")
        elif leaf == "kwota" and (isinstance(value, bool) or not isinstance(value, (int, float))):
            errors.append(f"{path}: kwota musi być liczbą ({value!r})")
        elif leaf == "czynnosc_powod_id" and (isinstance(value, bool) or value not in range(1, 13)):
            errors.append(f"{path}: id powodu spoza słownika UZP 1–12 ({value!r})")
    return errors
