"""evaluate.py — liczy F1 DOKŁADNIE wg definicji UZP (Discord, 11:26).

  TP — wartość w predykcji zgodna z ground truth
        (długie stringi: difflib.SequenceMatcher >= 0.80).
  TN — wartości nie ma ani w predykcji, ani w ground truth.
  FP — wartość tylko w predykcji, albo w obu, ale niezgodna.
  FN — wartości nie ma w predykcji, a jest w ground truth.
  F1 = 2TP / (2TP + FP + FN)

JSON-e spłaszczamy do liści (każde pole = jeden punkt), np.
  OdwolanieKIO.czynnosc_powod_id, Odwolujacy[0].nip, przelew_bankowy.kwota.
Raport: per pole, per typ dokumentu i łącznie.

Użycie:
  python evaluate.py <folder_z_danymi_treningowymi> [folder_z_predykcjami]
  Domyślnie predykcje czyta z ./output_test (albo ./output).
  Mapowanie: dane/przyklad_01/odwolanie.json <-> output/case1/odwolanie.json
  (nazwa pliku bez rozszerzenia musi się zgadzać; foldery case'ów
  dopasowujemy po kolei alfabetycznie, gdy nazwy się różnią).
"""

import difflib
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict


def flatten(obj, prefix=""):
    """Spłaszcz JSON do {ścieżka: wartość-liść}."""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = obj
    return out


def _is_long_string(v):
    return isinstance(v, str) and len(v) >= 15


def _match(pred, truth):
    """True, gdy predykcja liczy się jako zgodna z ground truth."""
    if isinstance(pred, bool) or isinstance(truth, bool):
        return pred is truth
    if isinstance(pred, (int, float)) and isinstance(truth, (int, float)):
        return float(pred) == float(truth)
    if isinstance(pred, str) and isinstance(truth, str):
        p, t = " ".join(pred.split()), " ".join(truth.split())
        if _is_long_string(p) or _is_long_string(t):
            return difflib.SequenceMatcher(None, p, t).ratio() >= 0.80
        return p == t
    return pred == truth


def score_pair(pred, truth):
    """Zwraca (TP, TN, FP, FN) dla jednej pary JSON-ów."""
    pf, tf = flatten(pred), flatten(truth)
    keys = set(pf) | set(tf)
    tp = tn = fp = fn = 0
    per_field = {}
    for k in keys:
        p, t = pf.get(k, "__MISSING__"), tf.get(k, "__MISSING__")
        if p == "__MISSING__" and t == "__MISSING__":
            continue
        if p is None and t is None:
            tn += 1
            per_field[k] = "TN"
        elif p is None and t is not None:
            fn += 1
            per_field[k] = "FN"
        elif p is not None and t is None:
            fp += 1
            per_field[k] = "FP"
        elif _match(p, t):
            tp += 1
            per_field[k] = "TP"
        else:
            fp += 1
            fn += 1
            per_field[k] = "FP+FN"
    return (tp, tn, fp, fn), per_field


def f1(tp, fp, fn):
    return 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0


def _doc_type(path, data=None):
    p = path.lower()
    if "odwolan" in p:
        return "odwolanie"
    if "pelnomocnictw" in p:
        return "pelnomocnictwo"
    if "przelew" in p or "potwierdzen" in p:
        return "przelew"
    if "przystap" in p:
        return "przystapienie"
    if data:
        if "OdwolanieKIO" in data:
            return "odwolanie"
        if "mocodawca" in data:
            return "pelnomocnictwo"
        if "przelew_bankowy" in data:
            return "przelew"
        if "przystepujacy" in data:
            return "przystapienie"
    return "inny"


def main():
    truth_dir = sys.argv[1] if len(sys.argv) > 1 else "dane"
    pred_dir = sys.argv[2] if len(sys.argv) > 2 else ("output_test" if os.path.isdir("output_test") else "output")
    truth_files = sorted(glob.glob(os.path.join(truth_dir, "**", "*.json"), recursive=True))
    pred_files = glob.glob(os.path.join(pred_dir, "**", "*.json"), recursive=True)

    def _case_no(path):
        m = re.search(r"(?:przyklad_|case)(\d+)", path)
        return int(m.group(1)) if m else None

    def _find_pred(truth_path):
        rel = os.path.relpath(truth_path, truth_dir)
        direct = os.path.join(pred_dir, rel)
        if os.path.isfile(direct):
            return direct
        base = os.path.basename(truth_path)
        case_no = _case_no(truth_path)
        cands = []
        for pf in pred_files:
            if _case_no(pf) != case_no:
                continue
            pb = os.path.basename(pf)
            if pb == base:
                return pf
            if base in ("przelew.json", "potwierdzenie_przelewu.json") and pb in ("potwierdzenie_przelewu.json", "przelew.json"):
                cands.append(pf)
        return cands[0] if cands else None

    total = Counter()
    by_type = defaultdict(Counter)
    field_stats = defaultdict(Counter)
    worst = Counter()
    acc = Counter()  # rachunki bankowe: dokładne porównanie (informacyjnie, F1 bez zmian)
    acc_other = []
    # Raport wsadowy app.py (<predykcje>_raport.json): który dokument szedł przez OCR i jaki silnik OCR.
    rep_path = os.path.normpath(pred_dir).rstrip("\\/") + "_raport.json"
    ocr_of, ocr_engine = {}, None
    if os.path.isfile(rep_path):
        rep = json.load(open(rep_path, encoding="utf-8"))
        ocr_engine = (rep.get("podsumowanie") or {}).get("silnik_ocr")
        ocr_of = {os.path.normpath(r["wynik"]): bool(r.get("ocr")) for r in rep.get("dokumenty", []) if r.get("wynik")}
    by_src = defaultdict(Counter)
    errors = []
    n_docs = 0
    missing_pred = 0
    for tf in truth_files:
        name = os.path.basename(tf)
        pf = _find_pred(tf)
        if not pf:
            missing_pred += 1
            continue
        truth = json.load(open(tf, encoding="utf-8"))
        pred = json.load(open(pf, encoding="utf-8"))
        (tp, tn, fp, fn), per_field = score_pair(pred, truth)
        total.update({"TP": tp, "TN": tn, "FP": fp, "FN": fn})
        by_type[_doc_type(tf, truth)].update({"TP": tp, "TN": tn, "FP": fp, "FN": fn})
        src = ocr_of.get(os.path.normpath(os.path.relpath(pf, pred_dir)))
        if src is not None:
            by_src["skan (OCR)" if src else "PDF tekstowy"].update({"TP": tp, "TN": tn, "FP": fp, "FN": fn, "dok": 1})
        for field, res in per_field.items():
            base = field.split("[")[0].rsplit(".", 1)[-1] if "." in field else field
            field_stats[field][res] += 1
            if res in ("FN", "FP", "FP+FN"):
                worst[field] += 1
        pflat, tflat = flatten(pred), flatten(truth)
        for field, res in sorted(per_field.items()):
            if res in ("FN", "FP", "FP+FN"):
                errors.append(f"{os.path.relpath(tf, truth_dir).replace(os.sep, '/')} | `{field}` | {res} | "
                              f"oczekiwane: {tflat.get(field)!r} | otrzymane: {pflat.get(field)!r}")
        for field in sorted(set(pflat) | set(tflat)):
            if field.rsplit(".", 1)[-1] not in ("z_rachunku", "na_rachunek"):
                continue
            p, t = pflat.get(field), tflat.get(field)
            if p is None and t is None:
                continue
            acc["pol"] += 1
            if p == t:
                acc["identyczne"] += 1
                acc["wzorzec_z_prefiksem"] += bool(re.match(r"[A-Z]{2}", t or ""))
            elif isinstance(p, str) and isinstance(t, str) and re.fullmatch(r"[A-Z]{2}" + re.escape(t), p):
                acc["tylko_prefiks"] += 1  # dokument ma prefiks kraju, wzorzec UZP go nie ma
            else:
                acc["inne"] += 1
                acc_other.append(f"{os.path.relpath(tf, truth_dir).replace(os.sep, '/')} {field}: wynik={p!r}, wzorzec={t!r}")
        n_docs += 1

    # Dokumenty negatywne: PDF bez pliku ground truth -> oczekiwany pusty JSON {}.
    # Każda niepusta wartość w predykcji dla negatywu to FP (wg definicji UZP).
    neg_total, neg_ok, neg_fp, neg_bad = 0, 0, 0, []
    for pdf in sorted(glob.glob(os.path.join(truth_dir, "**", "*.pdf"), recursive=True)):
        stem = os.path.splitext(pdf)[0]
        if os.path.isfile(stem + ".json"):
            continue
        if os.path.basename(stem) == "potwierdzenie_przelewu" and os.path.isfile(
                os.path.join(os.path.dirname(pdf), "przelew.json")):
            continue
        neg_total += 1
        # relpath na pełnej nazwie PDF-a: Windows obcina końcową kropkę ("...r..pdf" -> "...r.").
        rel = os.path.splitext(os.path.relpath(pdf, truth_dir))[0] + ".json"
        pp = os.path.join(pred_dir, rel)
        pred = json.load(open(pp, encoding="utf-8")) if os.path.isfile(pp) else None
        if pred == {}:
            neg_ok += 1
        else:
            fp = sum(1 for v in flatten(pred or {}).values() if v is not None)
            neg_fp += fp
            neg_bad.append(f"{rel}: {'brak pliku JSON' if pred is None else f'{fp} wartości (FP)'}")

    lines = []
    lines.append("# Wynik ewaluacji F1 (definicja UZP, Discord 11:26)")
    lines.append("")
    if ocr_engine:
        lines.append(f"Przebieg: predykcje `{os.path.basename(os.path.normpath(pred_dir))}`, silnik OCR: {ocr_engine}")
        lines.append("")
    lines.append(f"Dokumentów porównanych: {n_docs}, brak predykcji dla: {missing_pred}")
    lines.append(f"TP={total['TP']} TN={total['TN']} FP={total['FP']} FN={total['FN']}")
    lines.append(f"**F1 łączny: {f1(total['TP'], total['FP'], total['FN']):.4f}**")
    lines.append("")
    if neg_total:
        lines.append("## Dokumenty negatywne (oczekiwany pusty JSON {})")
        lines.append(f"- poprawnie puste: {neg_ok}/{neg_total}, FP z niepustych negatywów: {neg_fp}")
        lines.append(f"- F1 łączny z negatywami: {f1(total['TP'], total['FP'] + neg_fp, total['FN']):.4f}")
        for b in neg_bad:
            lines.append(f"  - {b}")
        lines.append("")
    lines.append("## F1 per typ dokumentu")
    for dtype, c in sorted(by_type.items()):
        lines.append(f"- {dtype}: F1={f1(c['TP'], c['FP'], c['FN']):.4f} (TP={c['TP']} FP={c['FP']} FN={c['FN']} TN={c['TN']})")
    lines.append("")
    if by_src:
        lines.append(f"## F1 wg rodzaju PDF-a (raport wsadowy `{os.path.basename(rep_path)}`)")
        lines.append(f"- silnik OCR w tym przebiegu: {ocr_engine or 'nieznany'}")
        for src, c in sorted(by_src.items()):
            lines.append(f"- {src}: F1={f1(c['TP'], c['FP'], c['FN']):.4f} (dokumentów={c['dok']}, "
                         f"TP={c['TP']} FP={c['FP']} FN={c['FN']} TN={c['TN']})")
        lines.append("- Wynik skanów zależy od wersji silnika OCR, nie od reguł ekstrakcji: ten sam kod i ten sam model "
                     "`pol.traineddata` dają różny tekst skanu na Tesseracie 5.4.0, 5.5.0 i 5.3.4. Porównanie: "
                     "TECHNICAL.md, sekcja „OCR: wersja silnika a wynik”.")
        lines.append("")
    lines.append("## Wszystkie błędy (dokument | pole | rodzaj | oczekiwane | otrzymane)")
    lines.extend(f"- {e}" for e in errors) if errors else lines.append("- brak")
    lines.append("")
    if acc["pol"]:
        lines.append("## Rachunki bankowe a prefiks kraju (UZP, Discord 21:17 i 23:00)")
        lines.append("Numer rachunku zapisujemy dokładnie jak w dokumencie, usuwając wyłącznie białe znaki — "
                     "z prefiksem kraju (np. `PL`), jeśli dokument go podaje.")
        lines.append(f"- pól rachunków: {acc['pol']}; identycznych z wzorcem: {acc['identyczne']} "
                     f"(w tym z prefiksem we wzorcu: {acc['wzorzec_z_prefiksem']}); "
                     f"różnica wyłącznie w prefiksie kraju: {acc['tylko_prefiks']}; inna różnica: {acc['inne']}")
        if acc["tylko_prefiks"]:
            lines.append(f"- **Znany błąd wzorców UZP:** w {acc['tylko_prefiks']} polach dokument PDF zawiera prefiks "
                         "`PL`, a wzorcowy JSON UZP go nie ma. UZP potwierdziło (21:17, 23:00), że prefiks ma zostać, "
                         "dlatego wynik go zachowuje — tej poprawki nie cofamy. Wzorce, które mają prefiks, "
                         "zgadzają się z wynikiem co do znaku.")
            lines.append("- F1 wg definicji UZP się nie zmienia: numer z prefiksem (`PL` + 26 cyfr) ma podobieństwo "
                         "0,963 ≥ 0,80 do wzorca bez prefiksu. Przy ścisłym porównaniu (bez progu 0,80) "
                         f"te {acc['tylko_prefiks']} pól liczyłoby się jako FP+FN wyłącznie z powodu błędu wzorców.")
        if acc_other:
            lines.append("- inne różnice (np. błędnie odczytana cyfra w skanie), liczone zwykłą metryką UZP:")
            lines.extend(f"  - {o}" for o in acc_other)
        lines.append("")
    lines.append("## Najgorsze pola (liczba błędów FP/FN/FP+FN)")
    for field, cnt in worst.most_common(15):
        s = field_stats[field]
        lines.append(f"- {field}: błędy={cnt} (TP={s['TP']} FP={s['FP']} FN={s['FN']} FP+FN={s['FP+FN']} TN={s['TN']})")
    lines.append("")
    lines.append("## F1 per pole (top 30 pól)")
    per_field_f1 = []
    for field, s in field_stats.items():
        tp, fp, fn = s["TP"], s["FP"] + s["FP+FN"], s["FN"] + s["FP+FN"]
        per_field_f1.append((f1(tp, fp, fn), field, tp, fp, fn))
    for score, field, tp, fp, fn in sorted(per_field_f1)[:30]:
        lines.append(f"- {field}: F1={score:.4f} (TP={tp} FP={fp} FN={fn})")
    report = "\n".join(lines) + "\n"
    print(report)
    with open("EVAL.md", "w", encoding="utf-8") as fh:
        fh.write(report)
    print("Zapisano EVAL.md")


if __name__ == "__main__":
    main()
