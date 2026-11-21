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
        for field, res in per_field.items():
            base = field.split("[")[0].rsplit(".", 1)[-1] if "." in field else field
            field_stats[field][res] += 1
            if res in ("FN", "FP", "FP+FN"):
                worst[field] += 1
        n_docs += 1
    lines = []
    lines.append("# Wynik ewaluacji F1 (definicja UZP, Discord 11:26)")
    lines.append("")
    lines.append(f"Dokumentów porównanych: {n_docs}, brak predykcji dla: {missing_pred}")
    lines.append(f"TP={total['TP']} TN={total['TN']} FP={total['FP']} FN={total['FN']}")
    lines.append(f"**F1 łączny: {f1(total['TP'], total['FP'], total['FN']):.4f}**")
    lines.append("")
    lines.append("## F1 per typ dokumentu")
    for dtype, c in sorted(by_type.items()):
        lines.append(f"- {dtype}: F1={f1(c['TP'], c['FP'], c['FN']):.4f} (TP={c['TP']} FP={c['FP']} FN={c['FN']} TN={c['TN']})")
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
