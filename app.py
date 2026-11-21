"""Odwołania2JSON (UZP / HackNation 2026) — CLI + GUI.

Użycie:
  python app.py input output            # batch: PDF-e z input/ -> JSON-e do output/
  python app.py input output --no-llm   # tylko regexy + heurystyka (zero wywołań LLM-a)
  python app.py --serve [--port 8000]   # GUI: dokument z pochodzeniem pól + widok sprawy

Wymagania: Python 3.13+, pip install -r requirements.txt
Klucz API (open-source LLM po API z dostawcą z UE):
  LLM_API_KEY lub MISTRAL_API_KEY, opcjonalnie LLM_API_BASE / LLM_MODEL.
Bez klucza API aplikacja działa w trybie deterministycznym (regex + heurystyka).
"""

import argparse
import copy
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from case_consistency import case_checks, case_summary, check_case  # noqa: E402
from extractor import extract_document, load_config  # noqa: E402

MAX_UPLOAD = 60 * 1024 * 1024  # łącznie na jedno żądanie GUI


def process_file(pdf_path, config, use_llm=True, trace=None):
    result, doc_type, info = extract_document(pdf_path, config, use_llm=use_llm, trace=trace)
    return result, doc_type, info


def process_folder(input_dir, output_dir, use_llm=True):
    config = load_config()
    os.makedirs(output_dir, exist_ok=True)
    stats = {"ok": 0, "err": 0}
    # Grupowanie po folderze sprawy (case) — do spójności między dokumentami.
    cases = {}
    case_docs = {}
    for root, _dirs, files in os.walk(input_dir):
        for fname in sorted(files):
            if not fname.lower().endswith(".pdf"):
                continue
            pdf_path = os.path.join(root, fname)
            rel_case = os.path.relpath(root, input_dir)
            rel_case = "" if rel_case == "." else rel_case
            out_case_dir = os.path.join(output_dir, rel_case)
            os.makedirs(out_case_dir, exist_ok=True)
            out_path = os.path.join(out_case_dir, os.path.splitext(fname)[0] + ".json")
            try:
                trace = {}
                result, doc_type, info = process_file(pdf_path, config, use_llm=use_llm, trace=trace)
                if doc_type == "nieznany":
                    stats["unknown"] = stats.get("unknown", 0) + 1
                    print(f"[ODRZUCONO] {pdf_path}: nie udało się znaleźć typu dokumentu (typ: nieznany) -> pusty JSON {{}}")
                    with open(out_path, "w", encoding="utf-8") as fh:
                        json.dump({}, fh, ensure_ascii=False, indent=2)
                    continue
                with open(out_path, "w", encoding="utf-8") as fh:
                    json.dump(result, fh, ensure_ascii=False, indent=2)
                case_results = cases.setdefault(rel_case, {})
                if doc_type in case_results:
                    print(f"[WARN] {pdf_path}: drugi dokument typu {doc_type} w sprawie {rel_case or '.'} — zostawiam pierwszy")
                else:
                    case_results[doc_type] = (result, out_path)
                case_docs.setdefault(rel_case, []).append(
                    {"type": doc_type, "result": result, "text": trace.get("text", ""), "filename": fname})
                stats["ok"] += 1
                print(f"[OK] {pdf_path} -> {out_path} ({doc_type}, ocr={info.get('ocr')})")
            except Exception as exc:  # noqa: BLE001 — jeden zły PDF nie zatrzymuje batcha
                stats["err"] += 1
                print(f"[ERR] {pdf_path}: {exc} -> pusty JSON {{}}")
                # Każdy plik wejściowy ma swój JSON w output (wymóg UZP); błąd = brak danych.
                with open(out_path, "w", encoding="utf-8") as fh:
                    json.dump({}, fh, ensure_ascii=False, indent=2)
    # Spójność sprawy: uzupełnij braki z innych dokumentów tej samej sprawy.
    for case, case_results in cases.items():
        if len(case_results) >= 2:
            results = {dt: res for dt, (res, _p) in case_results.items()}
            _fixed, uwagi = check_case(results)
            for u in uwagi:
                print(f"[sprawa {case or '.'}] {u}")
            for doc_type, fixed in _fixed.items():
                out_path = case_results[doc_type][1]
                with open(out_path, "w", encoding="utf-8") as fh:
                    json.dump(fixed, fh, ensure_ascii=False, indent=2)
            # Kontrole spójności (informacyjnie, nie zmieniają JSON-ów).
            for ch in case_checks(case_docs.get(case, [])):
                print(f"[sprawa {case or '.'}] {ch['status']:5s} {ch['nazwa']}: {ch['opis']}")
    print(f"Gotowe: OK={stats['ok']} ERR={stats['err']}")
    return stats


# ---------------------------------------------------------------- GUI (serwer HTTP)
def _parse_multipart(content_type, body):
    """multipart/form-data -> [{field, filename, data}] (zamiennik modułu cgi, usuniętego w Pythonie 3.13)."""
    m = re.search(r'boundary=(?:"([^"]+)"|([^;\s]+))', content_type or "")
    if not m:
        return []
    boundary = b"--" + (m.group(1) or m.group(2)).encode("latin-1")
    parts = []
    for chunk in body.split(boundary)[1:]:
        if chunk.startswith(b"--"):
            break
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]
        head, sep, data = chunk.partition(b"\r\n\r\n")
        if not sep:
            continue
        if data.endswith(b"\r\n"):
            data = data[:-2]
        disp = re.search(r"content-disposition:([^\r\n]*)", head.decode("utf-8", "replace"), re.IGNORECASE)
        disp = disp.group(1) if disp else ""
        fn = re.search(r'filename="([^"]*)"', disp)
        name = re.search(r'\bname="([^"]*)"', disp)
        parts.append({"field": name.group(1) if name else None,
                      "filename": fn.group(1) if fn else None, "data": data})
    return parts


def _analyze_upload(data, config, use_llm):
    """Zapis do pliku tymczasowego (unikalnego per żądanie), ekstrakcja, sprzątanie."""
    fd, tmp = tempfile.mkstemp(suffix=".pdf", prefix="odwolania2json_")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        trace = {}
        result, doc_type, info = extract_document(tmp, config, use_llm=use_llm, trace=trace)
        return result, doc_type, info, trace
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _type_label(config, doc_type):
    if doc_type == "nieznany":
        return "Dokument obcy — pusty JSON {}"
    return (config.get("classes", {}).get(doc_type) or {}).get("label", doc_type)


def _public_info(info):
    return {k: info.get(k) for k in ("ocr", "pages", "chars", "needs_ocr", "error") if info.get(k) is not None}


def serve(port=8000, use_llm=True):
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from provenance import build_provenance, flatten

    config = load_config()
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

    def doc_payload(filename, result, doc_type, info, trace, filled_from=None):
        prov = build_provenance(result, doc_type, trace, config, filled_from) if doc_type != "nieznany" else []
        return {"filename": filename, "type": doc_type, "label": _type_label(config, doc_type),
                "info": _public_info(info), "result": result, "provenance": prov}

    def analyze_case(files):
        docs = []
        for f in files:
            result, doc_type, info, trace = _analyze_upload(f["data"], config, use_llm)
            docs.append({"filename": f["filename"], "type": doc_type, "result": result,
                         "info": info, "trace": trace, "text": trace.get("text", "")})
        known = [d for d in docs if d["type"] != "nieznany"]
        # Spójność sprawy jak w trybie wsadowym: braki uzupełniane z innego dokumentu sprawy.
        first = {}
        for d in known:
            first.setdefault(d["type"], d)
        before = {t: copy.deepcopy(d["result"]) for t, d in first.items()}
        _fixed, uwagi = check_case({t: d["result"] for t, d in first.items()})
        source_of = {"pelnomocnictwo": first.get("odwolanie"), "odwolanie": first.get("pelnomocnictwo")}
        filled = {}
        for t, d in first.items():
            b, a = dict(flatten(before[t])), dict(flatten(d["result"]))
            src = source_of.get(t)
            filled[id(d)] = {p: (src["filename"] if src else "inny dokument") for p in a if a[p] != b.get(p)}
        return {
            "documents": [doc_payload(d["filename"], d["result"], d["type"], d["info"], d["trace"],
                                      filled.get(id(d))) for d in docs],
            "summary": case_summary(known),
            "checks": case_checks(known),
            "uwagi": uwagi,
        }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send_file(os.path.join(static_dir, "index.html"), "text/html; charset=utf-8")
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if self.path not in ("/api/extract", "/api/case"):
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                self._send_json({"error": "brak pliku PDF w żądaniu"}, 400)
                return
            if length > MAX_UPLOAD:
                self._send_json({"error": f"pliki za duże (limit {MAX_UPLOAD // 2**20} MB na żądanie)"}, 413)
                return
            body = self.rfile.read(length)
            # Kolejność plików = kolejność w GUI (skany mogą mieć identyczne nazwy, np. 0001.pdf).
            files = [p for p in _parse_multipart(self.headers.get("Content-Type"), body) if p["filename"]]
            if not files:
                self._send_json({"error": "brak pliku PDF w żądaniu"}, 400)
                return
            try:
                if self.path == "/api/extract":
                    f = files[0]
                    result, doc_type, info, trace = _analyze_upload(f["data"], config, use_llm)
                    self._send_json(doc_payload(f["filename"], result, doc_type, info, trace))
                else:
                    self._send_json(analyze_case(files))
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)

        def _send_file(self, path, ctype):
            with open(path, "rb") as fh:
                data = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, obj, code=200):
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    print(f"GUI: http://127.0.0.1:{port}  (Ctrl+C, aby zatrzymać)")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


def main():
    ap = argparse.ArgumentParser(description="Odwołania2JSON — PDF sprawy KIO -> JSON")
    ap.add_argument("input", nargs="?", default="input")
    ap.add_argument("output", nargs="?", default="output")
    ap.add_argument("--serve", action="store_true", help="uruchom GUI")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-llm", action="store_true", help="tylko regexy, bez LLM-a")
    args = ap.parse_args()
    if args.no_llm:
        # Wyłącza KAŻDE wywołanie LLM-a, także normalizację nazwisk do mianownika.
        os.environ["LLM_OFF"] = "1"
    if args.serve:
        serve(args.port, use_llm=not args.no_llm)
    elif os.path.isfile(args.input):
        config = load_config()
        out_path = args.output
        if os.path.isdir(out_path) or (not out_path.endswith(".json") and not os.path.exists(out_path)):
            os.makedirs(out_path, exist_ok=True)
            out_path = os.path.join(out_path, os.path.splitext(os.path.basename(args.input))[0] + ".json")
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        try:
            result, doc_type, info = process_file(args.input, config, use_llm=not args.no_llm)
        except Exception as exc:  # noqa: BLE001
            print(f"[ERR] {args.input}: {exc} -> pusty JSON {{}}")
            result, doc_type, info = {}, "nieznany", {}
        if doc_type == "nieznany" or result is None:
            # Dokument obcy -> pusty JSON {} (tak samo jak w trybie wsadowym).
            print(f"[ODRZUCONO] {args.input}: nie udało się znaleźć typu dokumentu (typ: nieznany) -> pusty JSON {{}}")
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump({}, fh, ensure_ascii=False, indent=2)
            sys.exit(0)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
        print(f"[OK] {args.input} -> {out_path} ({doc_type}, ocr={info.get('ocr')})")
    else:
        process_folder(args.input, args.output, use_llm=not args.no_llm)


if __name__ == "__main__":
    main()
