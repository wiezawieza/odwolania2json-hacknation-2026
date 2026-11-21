"""Kreator nowego typu dokumentu (GUI) — formularz -> wpis klasy w YAML -> test na PDF-ie.

Kreator NIE dodaje kodu: składa klasę z tych samych budulców, których używa silnik
deklaratywny (`extract` w config/document_types.yaml, wzór: przystapienie).
Zapis: config/custom/<id>.yaml, wczytywany przez load_config() przy starcie
(klasy z document_types.yaml mają pierwszeństwo i nie są nadpisywane).
Test na PDF-ie działa bez LLM-a — pokazuje czysty wynik reguł.
"""

import copy
import os
import re

import yaml

from extractor import EXTRACTORS, classify, classify_scores, extract_generic, load_config

CUSTOM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "custom")

# Budulce dostępne w formularzu: rule -> (etykieta, opis parametru 1, opis parametru 2).
RULES = {
    "header_date": ("Data z nagłówka „Miejscowość, data”", None, None),
    "header_place": ("Miejscowość z nagłówka", None, None),
    "party_name": ("Nazwa strony (blok od etykiety)", "etykiety strony, np. Wnioskodawca: | Wykonawca:",
                   "koniec bloku — etykiety, np. Zamawiający | Dotyczy"),
    "party_id:nip": ("NIP strony (10 cyfr)", "etykiety strony", "koniec bloku — etykiety"),
    "party_id:krs": ("KRS strony (10 cyfr)", "etykiety strony", "koniec bloku — etykiety"),
    "party_id:regon": ("REGON strony (9/14 cyfr)", "etykiety strony", "koniec bloku — etykiety"),
    "party_id:pesel": ("PESEL strony (11 cyfr)", "etykiety strony", "koniec bloku — etykiety"),
    "numer_postepowania": ("Numer postępowania / znak sprawy", "etykiety (regex, |) — puste = domyślne", None),
    "regex": ("Własny wzorzec (regex, grupa 1)", "wzorzec, np. Kwota:\\s*([\\d ,]+)",
              "normalizacja: digits | nospace | date | amount | upper | lower (puste = spacje)"),
    "choice": ("Wybór jednej z wartości", "wartość = wzorzec; wartość = wzorzec", None),
    "signers": ("Osoby podpisujące (lista, mianownik)", None, None),
    "const": ("Stała wartość", "wartość", None),
}
NORMALIZE = {"", "digits", "nospace", "date", "amount", "upper", "lower"}
ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
PATH_RE = re.compile(r"^[A-Za-z_][\w\-]*(\.[A-Za-z_][\w\-]*)*$")


def _split(s, sep="|"):
    return [x.strip() for x in str(s or "").split(sep) if x.strip()]


def rule_catalog():
    return [{"rule": k, "label": v[0], "p1": v[1], "p2": v[2]} for k, v in RULES.items()]


def build_class(form, config):
    """Formularz (dict z GUI) -> (id, klasa). Rzuca ValueError z listą błędów po polsku."""
    errors = []
    cid = str(form.get("id") or "").strip()
    if not ID_RE.match(cid):
        errors.append("identyfikator: małe litery, cyfry i _, 2–40 znaków, zaczyna się literą (np. wniosek_o_wpis)")
    elif cid in EXTRACTORS or cid == "nieznany":
        errors.append(f"identyfikator „{cid}” jest zajęty przez klasę wbudowaną")
    elif cid in config.get("classes", {}) and not os.path.isfile(os.path.join(CUSTOM_DIR, cid + ".yaml")):
        errors.append(f"identyfikator „{cid}” jest już zdefiniowany w config/document_types.yaml")
    label = str(form.get("label") or "").strip() or cid

    keywords = []
    for i, row in enumerate(form.get("keywords") or [], 1):
        phrase = str(row.get("phrase") or "").strip().lower()
        if not phrase:
            continue
        try:
            weight = int(row.get("weight") or 0)
        except (TypeError, ValueError):
            weight = 0
        if not 1 <= weight <= 10:
            errors.append(f"słowo kluczowe {i} („{phrase}”): waga musi być liczbą 1–10")
            continue
        keywords.append([phrase, weight])
    min_score = config.get("classify_min_score", 4)
    if sum(w for _p, w in keywords) < min_score:
        errors.append(f"słowa kluczowe: suma wag musi wynosić co najmniej {min_score} (próg klasyfikatora)")

    extract, seen = {}, set()
    for i, f in enumerate(form.get("fields") or [], 1):
        path = str(f.get("path") or "").strip()
        rule = str(f.get("rule") or "")
        if not path:
            continue
        where = f"pole {i} ({path})"
        if not PATH_RE.match(path):
            errors.append(f"{where}: ścieżka to klucze JSON oddzielone kropką, np. wnioskodawca.nip")
            continue
        if path in seen or any(path.startswith(s + ".") or s.startswith(path + ".") for s in seen):
            errors.append(f"{where}: ścieżka powtarza się lub koliduje z inną")
            continue
        if rule not in RULES:
            errors.append(f"{where}: nieznana reguła „{rule}”")
            continue
        p1, p2 = str(f.get("p1") or "").strip(), str(f.get("p2") or "").strip()
        spec = {"rule": rule.split(":")[0]}
        if rule.startswith("party_"):
            if not _split(p1):
                errors.append(f"{where}: podaj etykietę strony (np. Wnioskodawca:)")
                continue
            spec["label"] = _split(p1)
            spec["until"] = _split(p2) or ["Zamawiający", "Dotyczy"]
            if ":" in rule:
                spec["id"] = rule.split(":")[1]
        elif rule == "numer_postepowania" and p1:
            spec["labels"] = _split(p1)
        elif rule == "regex":
            try:
                rx = re.compile(p1)
            except re.error as exc:
                errors.append(f"{where}: błędny wzorzec regex ({exc})")
                continue
            if not p1 or rx.groups < 1:
                errors.append(f"{where}: wzorzec musi mieć grupę ( ) z wartością")
                continue
            if p2 not in NORMALIZE:
                errors.append(f"{where}: normalizacja musi być jedną z: {', '.join(sorted(NORMALIZE - {''}))}")
                continue
            spec["pattern"] = p1
            if p2:
                spec["normalize"] = p2
        elif rule == "choice":
            opts = []
            for part in _split(p1, ";"):
                val, _, pat = part.partition("=")
                val, pat = val.strip(), (pat.strip() or re.escape(val.strip()))
                try:
                    re.compile(pat)
                except re.error as exc:
                    errors.append(f"{where}: błędny wzorzec opcji „{val}” ({exc})")
                    continue
                if val:
                    opts.append([val, pat])
            if not opts:
                errors.append(f"{where}: podaj co najmniej jedną opcję „wartość = wzorzec”")
                continue
            spec["options"] = opts
        elif rule == "const":
            spec["value"] = p1 or None
        if f.get("llm") and rule != "const":
            spec["llm"] = True
        node = extract
        keys = path.split(".")
        for k in keys[:-1]:
            node = node.setdefault(k, {})
            if "rule" in node:
                errors.append(f"{where}: „{k}” jest już polem-liściem")
                break
        else:
            node[keys[-1]] = spec
            seen.add(path)
    if not seen:
        errors.append("dodaj co najmniej jedno pole JSON-a")
    if errors:
        raise ValueError(errors)

    cls = {
        "label": label,
        "weighted_keywords": keywords,
        # Ta sama lista sygnałów dokumentów obcych (orzeczenia KIO) co w klasach wbudowanych.
        "negative_keywords": copy.deepcopy(
            (config.get("classes", {}).get("odwolanie") or {}).get("negative_keywords") or []),
        "prompt_hint": str(form.get("prompt_hint") or "").strip() or f"Dokument typu: {label}.",
        "extract": extract,
    }
    return cid, cls


def to_yaml(cid, cls):
    header = (f"# Klasa dokumentu „{cls['label']}” — wygenerowana kreatorem w GUI.\n"
              "# Wczytywana automatycznie z config/custom/ (bez zmian w kodzie).\n")
    return header + yaml.safe_dump({cid: cls}, allow_unicode=True, sort_keys=False, width=120,
                                   default_flow_style=None)


def test_on_text(cid, cls, text, config):
    """Klasyfikacja z nową klasą + wynik jej reguł (zawsze, także gdy wygrała inna klasa)."""
    from llm import llm_disabled

    tmp = copy.deepcopy(config)
    tmp.setdefault("classes", {})[cid] = cls
    scores = classify_scores(text, tmp)
    with llm_disabled():
        result = extract_generic(text, tmp, cid, {})
    return tmp, {
        "classified_as": classify(text, tmp),
        "scores": dict(sorted(scores.items(), key=lambda kv: -kv[1])),
        "min_score": tmp.get("classify_min_score", 4),
        "result": result,
    }


def save_class(cid, cls):
    os.makedirs(CUSTOM_DIR, exist_ok=True)
    path = os.path.join(CUSTOM_DIR, cid + ".yaml")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(to_yaml(cid, cls))
    return path, load_config()
