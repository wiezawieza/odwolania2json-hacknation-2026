"""Odwołania2JSON (UZP / HackNation 2026) — CLI + minimalne GUI.

Użycie:
  python app.py input output            # batch: PDF-e z input/ -> JSON-e do output/
  python app.py input output --no-llm   # tylko regexy + heurystyka (bez LLM-a)
  python app.py --serve [--port 8000]   # GUI: wgraj PDF, podgląd obok JSON-a

Wymagania: Python 3.13+, pip install -r requirements.txt
Klucz API (open-source LLM po API z dostawcą z UE):
  LLM_API_KEY lub MISTRAL_API_KEY, opcjonalnie LLM_API_BASE / LLM_MODEL.
Bez klucza i bez Ollamy aplikacja działa w trybie regex+heurystyka.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from case_consistency import check_case  # noqa: E402
from extractor import classify, extract_document, load_config  # noqa: E402


def process_file(pdf_path, config, use_llm=True):
    result, doc_type, info = extract_document(pdf_path, config, use_llm=use_llm)
    return result, doc_type, info


def process_folder(input_dir, output_dir, use_llm=True):
    config = load_config()
    os.makedirs(output_dir, exist_ok=True)
    stats = {"ok": 0, "err": 0}
    # Grupowanie po folderze sprawy (case) — do spójności między dokumentami.
    cases = {}
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
                result, doc_type, info = process_file(pdf_path, config, use_llm=use_llm)
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
                stats["ok"] += 1
                print(f"[OK] {pdf_path} -> {out_path} ({doc_type}, ocr={info.get('ocr')})")
            except Exception as exc:  # noqa: BLE001 — jeden zły PDF nie zatrzymuje batcha
                stats["err"] += 1
                print(f"[ERR] {pdf_path}: {exc}")
                if os.path.exists(out_path):
                    os.remove(out_path)
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
    print(f"Gotowe: OK={stats['ok']} ERR={stats['err']}")
    return stats


def serve(port=8000):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import cgi

    config = load_config()
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send_file(os.path.join(static_dir, "index.html"), "text/html")
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if self.path != "/api/extract":
                self.send_response(404)
                self.end_headers()
                return
            form = cgi.FieldStorage(fp=self.rfile, headers=self.headers,
                                    environ={"REQUEST_METHOD": "POST"})
            item = form["file"] if "file" in form else None
            if item is None or not item.filename:
                self._send_json({"error": "brak pliku"}, 400)
                return
            tmp = os.path.join("/tmp", "hn_upload.pdf")
            with open(tmp, "wb") as fh:
                fh.write(item.file.read())
            try:
                result, doc_type, info = extract_document(tmp, config, use_llm=True)
                self._send_json({"type": doc_type, "info": info, "result": result})
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, 500)

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
    if args.serve:
        serve(args.port)
    elif os.path.isfile(args.input):
        config = load_config()
        out_path = args.output
        if os.path.isdir(out_path) or (not out_path.endswith(".json") and not os.path.exists(out_path)):
            os.makedirs(out_path, exist_ok=True)
            out_path = os.path.join(out_path, os.path.splitext(os.path.basename(args.input))[0] + ".json")
        result, doc_type, info = process_file(args.input, config, use_llm=not args.no_llm)
        if doc_type == "nieznany" or result is None:
            print(f"[ODRZUCONO] {args.input}: nie udało się znaleźć typu dokumentu (typ: nieznany)")
            if os.path.exists(out_path):
                os.remove(out_path)
            sys.exit(0)
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
        print(f"[OK] {args.input} -> {out_path} ({doc_type}, ocr={info.get('ocr')})")
    else:
        process_folder(args.input, args.output, use_llm=not args.no_llm)


if __name__ == "__main__":
    main()
