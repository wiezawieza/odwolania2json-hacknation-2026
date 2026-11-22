"""Odwołania2JSON (UZP / HackNation 2026) — CLI + GUI.

Użycie:
  python app.py input output            # batch: PDF-e z input/ -> JSON-e do output/ (2 procesy robocze)
  python app.py input output --no-llm   # tylko regexy + heurystyka (zero wywołań LLM-a)
  python app.py input output --workers 1   # batch w jednym procesie (wyniki identyczne, tylko wolniej)
  python app.py --serve [--port 8000] [--host 0.0.0.0]   # GUI: dokument z pochodzeniem pól + widok sprawy

Pipeline (tryb wsadowy):
  * równolegle: domyślnie min(2, liczba rdzeni) procesów roboczych, każdy dokument niezależnie;
    zapis i spójność sprawy w procesie głównym, więc JSON-y są identyczne jak przy --workers 1,
  * raport <output>_raport.json + .csv (lub --report PLIK): typ, status, czas każdego dokumentu,
    średni czas PDF tekstowy / skan OCR,
  * walidacja każdego JSON-a przed zapisem (validation.py),
  * exit code: 0 = wszystko OK (dokument obcy -> {} to też OK), 1 = co najmniej jeden
    błąd przetwarzania (ERR) lub walidacji (INVALID), 2 = brak wejścia.

Wymagania: Python 3.13+ (sprawdzone też na 3.12), pip install -r requirements.txt; na VM: RUN.md (Docker)
Klucz API (open-source LLM po API z dostawcą z UE):
  LLM_API_KEY lub MISTRAL_API_KEY, opcjonalnie LLM_API_BASE / LLM_MODEL.
Bez klucza API aplikacja działa w trybie deterministycznym (regex + heurystyka).
"""

import argparse
import copy
import csv
import json
import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from case_consistency import case_checks, case_summary, check_case  # noqa: E402
from extractor import extract_document, load_config, ocr_engine  # noqa: E402
from validation import validate_result  # noqa: E402

MAX_UPLOAD = 60 * 1024 * 1024  # łącznie na jedno żądanie GUI


def process_file(pdf_path, config, use_llm=True, trace=None):
    result, doc_type, info = extract_document(pdf_path, config, use_llm=use_llm, trace=trace)
    return result, doc_type, info


# ---------------------------------------------------------------- równoległość (tryb wsadowy)
DEFAULT_WORKERS = 2  # VM ewaluacyjna ma 2 vCPU; więcej: --workers N
_WORKER = {}


def available_cpus():
    """Rdzenie dostępne dla procesu (od Pythona 3.13 z uwzględnieniem affinity, np. --cpuset-cpus Dockera)."""
    if hasattr(os, "process_cpu_count"):
        return os.process_cpu_count() or 1
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0)) or 1
    return os.cpu_count() or 1


def _init_worker(config):
    """Start procesu roboczego: własna kopia konfiguracji, Tesseract z 1 wątkiem OpenMP.

    Workery już dzielą między siebie rdzenie, a dodatkowe wątki OpenMP Tesseracta na tych samych rdzeniach
    tylko spowalniają: na 2 rdzeniach strona skanu to 16,5 s z OpenMP i 4,7 s z 1 wątkiem, przy identycznym
    tekście (README, „Zmierzony czas przetwarzania”). Wartość ustawiona przez użytkownika ma pierwszeństwo.
    """
    _WORKER["config"] = config
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")


def _failed(exc, czas=0.0):
    return {"error": f"{type(exc).__name__}: {exc}", "error_msg": str(exc), "czas": czas}


def _extract_job(pdf_path, config, use_llm):
    """Ekstrakcja jednego PDF-a w procesie głównym (workers=1) albo roboczym; wynik przechodzi przez pickle."""
    t0 = time.perf_counter()
    try:
        trace = {}
        result, doc_type, info = process_file(pdf_path, config if config is not None else _WORKER["config"],
                                              use_llm=use_llm, trace=trace)
        return {"result": result, "doc_type": doc_type, "info": info, "text": trace.get("text", ""),
                "czas": time.perf_counter() - t0}
    except Exception as exc:  # noqa: BLE001 — jeden zły PDF nie zatrzymuje batcha
        return _failed(exc, time.perf_counter() - t0)


def _run_parallel(jobs, config, use_llm, n_workers):
    """Ekstrakcja w n_workers procesach roboczych; zwraca (indeks, wynik) w kolejności ukończenia.

    Największe pliki (zwykle wielostronicowe skany) idą pierwsze, żeby na końcu batch nie czekał na jeden
    długi skan. W locie jest najwyżej n_workers plików: gdy proces roboczy padnie (np. segfault biblioteki PDF
    albo brak pamięci), podejrzane są tylko te pliki — każdy idzie jeszcze raz we własnym procesie,
    a reszta dalej równolegle.
    """
    from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
    from concurrent.futures.process import BrokenProcessPool

    def new_pool(n):
        return ProcessPoolExecutor(max_workers=n, initializer=_init_worker, initargs=(config,))

    def size(i):
        try:
            return os.path.getsize(jobs[i]["pdf"])
        except OSError:
            return 0

    todo = sorted(range(len(jobs)), key=size)  # pop() zdejmuje największy
    suspects, inflight = [], {}
    pool = new_pool(n_workers)
    try:
        while todo or inflight:
            broken = False
            while todo and len(inflight) < n_workers and not broken:
                i = todo.pop()
                try:
                    inflight[pool.submit(_extract_job, jobs[i]["pdf"], None, use_llm)] = i
                except BrokenProcessPool:  # worker padł tuż przed zleceniem: plik nie wystartował
                    todo.append(i)
                    broken = True
            done = set()
            if inflight:
                done, _ = wait(inflight, return_when=FIRST_COMPLETED)
                broken = broken or any(isinstance(f.exception(), BrokenProcessPool) for f in done)
                if broken:
                    done, _ = wait(inflight)  # pula padła: pozostałe pliki w locie kończą się od razu
            for fut in done:
                i = inflight.pop(fut)
                exc = fut.exception()
                if isinstance(exc, BrokenProcessPool):
                    suspects.append(i)
                else:
                    yield i, (fut.result() if exc is None else _failed(exc))
            if broken:
                pool.shutdown(wait=True)
                pool = new_pool(n_workers)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    for i in sorted(suspects):
        single = new_pool(1)
        try:
            outcome = single.submit(_extract_job, jobs[i]["pdf"], None, use_llm).result()
        except BrokenProcessPool:
            outcome = _failed(RuntimeError("proces roboczy przerwany przy tym pliku (np. uszkodzony PDF)"))
        except Exception as exc:  # noqa: BLE001
            outcome = _failed(exc)
        finally:
            single.shutdown(wait=True)
        yield i, outcome


def _write_empty(out_path):
    """Pusty JSON {} — każdy plik wejściowy ma swój JSON w output (wymóg UZP); błąd = brak danych."""
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({}, fh, ensure_ascii=False, indent=2)


def _write_json(out_path, result, doc_type, config):
    """Walidacja przed zapisem; zapis zawsze (dane zostają do wglądu). Zwraca listę błędów walidacji."""
    errors = validate_result(result, doc_type, config)
    if errors and errors[0].startswith("wynik nie jest"):
        result = {}  # nie da się zapisać jako JSON -> pusty JSON, status ERR w raporcie
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    return errors


def _unreadable(pdf_path, info):
    """Opis błędu wejścia albo None (plik czytelny)."""
    try:
        with open(pdf_path, "rb") as fh:
            if not fh.read(1024).lstrip().startswith(b"%PDF-"):
                return "plik nie jest PDF-em (brak nagłówka %PDF-)"
    except OSError as exc:
        return f"nie można otworzyć pliku: {exc}"
    if not info.get("chars"):
        return ("brak tekstu — skan, a OCR (Tesseract) niedostępny" if info.get("needs_ocr")
                else "brak tekstu w PDF-ie (uszkodzony lub pusty plik)")
    return None


def _avg(xs):
    return round(sum(xs) / len(xs), 3) if xs else None


def write_report(rows, report_path, total_s, workers=1):
    """Raport wsadowy: JSON (pipeline) + CSV (Excel). Zwraca podsumowanie."""
    by = {}
    for r in rows:
        by[r["status"]] = by.get(r["status"], 0) + 1
    done = [r for r in rows if r["status"] in ("OK", "ODRZUCONO", "INVALID")]
    summary = {
        "dokumentow": len(rows), "statusy": by,
        "silnik_ocr": ocr_engine(),  # wynik skanów zależy od wersji Tesseracta (patrz EVAL.md)
        "workery": workers,  # czas_lacznie_s to czas zegarowy; czas_s dokumentu liczy jego własny worker
        "czas_lacznie_s": round(total_s, 2),
        "czas_sredni_s": _avg([r["czas_s"] for r in rows]),
        "czas_sredni_pdf_tekstowy_s": _avg([r["czas_s"] for r in done if not r["ocr"]]),
        "czas_sredni_skan_ocr_s": _avg([r["czas_s"] for r in done if r["ocr"]]),
        "czas_max_s": max((r["czas_s"] for r in rows), default=None),
    }
    os.makedirs(os.path.dirname(os.path.abspath(report_path)), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump({"podsumowanie": summary, "dokumenty": rows}, fh, ensure_ascii=False, indent=2)
    csv_path = os.path.splitext(report_path)[0] + ".csv"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:  # BOM: polskie znaki w Excelu
        w = csv.writer(fh, delimiter=";")
        w.writerow(["plik", "wynik", "typ", "status", "czas_s", "ocr", "strony", "bledy"])
        for r in rows:
            w.writerow([r["plik"], r["wynik"], r["typ"], r["status"], str(r["czas_s"]).replace(".", ","),
                        int(bool(r["ocr"])), r["strony"], " | ".join(r["bledy"])])
    return summary


def process_folder(input_dir, output_dir, use_llm=True, report_path=None, workers=None):
    """Tryb wsadowy. Zwraca statystyki; stats['exit_code'] != 0, gdy którykolwiek plik ma błąd.

    workers: liczba procesów roboczych (domyślnie min(2, dostępne rdzenie); 1 = jeden proces, bez puli).
    Każdy dokument jest ekstrahowany niezależnie. Zapis JSON-ów, statusy i spójność sprawy robi proces główny
    w kolejności plików jak przy jednym procesie, więc wyniki nie zależą od liczby workerów.
    """
    config = load_config()
    os.makedirs(output_dir, exist_ok=True)
    stats = {"ok": 0, "err": 0}
    rows, jobs = [], []
    t_start = time.perf_counter()
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
            row = {"plik": os.path.relpath(pdf_path, input_dir), "wynik": os.path.relpath(out_path, output_dir),
                   "typ": None, "status": None, "czas_s": None, "ocr": False, "strony": None, "bledy": []}
            rows.append(row)
            jobs.append({"pdf": pdf_path, "out": out_path, "fname": fname, "case": rel_case, "row": row})
    n_workers = min(workers or min(DEFAULT_WORKERS, available_cpus()), max(1, len(jobs)))
    ok_docs = {}  # indeks pliku -> (typ, wynik, tekst) do spójności sprawy

    def finish(i, outcome):
        """Zapis JSON-a, status i czas jednego dokumentu (zawsze w procesie głównym)."""
        job = jobs[i]
        pdf_path, out_path, row = job["pdf"], job["out"], job["row"]
        t0 = time.perf_counter()
        try:
            if "error" in outcome:  # wyjątek podczas ekstrakcji
                stats["err"] += 1
                row.update(status="ERR", bledy=[outcome["error"]])
                print(f"[ERR] {pdf_path}: {outcome['error_msg']} -> pusty JSON {{}}")
                _write_empty(out_path)
                return
            result, doc_type, info = outcome["result"], outcome["doc_type"], outcome["info"]
            row.update(typ=doc_type, ocr=bool(info.get("ocr")), strony=info.get("pages"))
            unreadable = _unreadable(pdf_path, info)
            if doc_type == "nieznany" and unreadable:
                # Uszkodzony / pusty plik / skan bez OCR: {} jak dla dokumentu obcego, ale to błąd wejścia.
                stats["err"] += 1
                row.update(status="ERR", bledy=[unreadable])
                print(f"[ERR] {pdf_path}: {unreadable} -> pusty JSON {{}}")
                _write_json(out_path, {}, "nieznany", config)
                return
            if doc_type == "nieznany":
                stats["unknown"] = stats.get("unknown", 0) + 1
                row["status"] = "ODRZUCONO"
                print(f"[ODRZUCONO] {pdf_path}: nie udało się znaleźć typu dokumentu (typ: nieznany) -> pusty JSON {{}}")
                _write_json(out_path, {}, "nieznany", config)
                return
            row["bledy"] = _write_json(out_path, result, doc_type, config)
            if row["bledy"]:
                row["status"] = "INVALID"
                stats["err"] += 1
                print(f"[INVALID] {pdf_path}: walidacja JSON: {'; '.join(row['bledy'])}")
                return
            row["status"] = "OK"
            ok_docs[i] = (doc_type, result, outcome["text"])
            stats["ok"] += 1
            print(f"[OK] {pdf_path} -> {out_path} ({doc_type}, ocr={info.get('ocr')}, "
                  f"{outcome['czas'] + time.perf_counter() - t0:.2f} s)")
        except Exception as exc:  # noqa: BLE001 — jeden zły PDF nie zatrzymuje batcha
            stats["err"] += 1
            row.update(status="ERR", bledy=[f"{type(exc).__name__}: {exc}"])
            print(f"[ERR] {pdf_path}: {exc} -> pusty JSON {{}}")
            _write_empty(out_path)
        finally:
            row["czas_s"] = round(outcome["czas"] + time.perf_counter() - t0, 3)

    if n_workers > 1:
        print(f"Przetwarzanie równoległe: {len(jobs)} PDF-ów, workery: {n_workers} "
              f"(Tesseract w workerze: OMP_THREAD_LIMIT={os.environ.get('OMP_THREAD_LIMIT', '1')})")
        for i, outcome in _run_parallel(jobs, config, use_llm, n_workers):
            finish(i, outcome)
    else:
        for i, job in enumerate(jobs):
            finish(i, _extract_job(job["pdf"], config, use_llm))
    # Grupowanie po folderze sprawy (case) w kolejności plików, niezależnie od kolejności ukończenia
    # w workerach: pierwszy dokument danego typu w sprawie wygrywa, jak przy jednym procesie.
    cases = {}
    case_docs = {}
    for i, job in enumerate(jobs):
        if i not in ok_docs:
            continue
        doc_type, result, text = ok_docs[i]
        case_results = cases.setdefault(job["case"], {})
        if doc_type in case_results:
            print(f"[WARN] {job['pdf']}: drugi dokument typu {doc_type} w sprawie {job['case'] or '.'} "
                  "— zostawiam pierwszy")
        else:
            case_results[doc_type] = (result, job["out"])
        case_docs.setdefault(job["case"], []).append(
            {"type": doc_type, "result": result, "text": text, "filename": job["fname"]})
    # Spójność sprawy: uzupełnij braki z innych dokumentów tej samej sprawy.
    for case, case_results in cases.items():
        if len(case_results) >= 2:
            results = {dt: res for dt, (res, _p) in case_results.items()}
            _fixed, uwagi = check_case(results)
            for u in uwagi:
                print(f"[sprawa {case or '.'}] {u}")
            for doc_type, fixed in _fixed.items():
                out_path = case_results[doc_type][1]
                errs = _write_json(out_path, fixed, doc_type, config)
                if errs:
                    row = next(r for r in rows if os.path.join(output_dir, r["wynik"]) == out_path)
                    row.update(status="INVALID", bledy=errs)
                    stats["ok"] -= 1
                    stats["err"] += 1
                    print(f"[INVALID] {out_path} po uzupełnieniu ze sprawy: {'; '.join(errs)}")
            # Kontrole spójności (informacyjnie, nie zmieniają JSON-ów).
            for ch in case_checks(case_docs.get(case, [])):
                print(f"[sprawa {case or '.'}] {ch['status']:5s} {ch['nazwa']}: {ch['opis']}")
    report_path = report_path or os.path.normpath(output_dir).rstrip("\\/") + "_raport.json"
    summary = write_report(rows, report_path, time.perf_counter() - t_start, n_workers)
    stats["exit_code"] = 1 if stats["err"] else 0
    print(f"Gotowe: OK={stats['ok']} ODRZUCONO={stats.get('unknown', 0)} ERR/INVALID={stats['err']} · "
          f"czas {summary['czas_lacznie_s']} s (workery: {n_workers}), dokument średnio {summary['czas_sredni_s']} s "
          f"(PDF tekstowy {summary['czas_sredni_pdf_tekstowy_s']} s, skan OCR {summary['czas_sredni_skan_ocr_s']} s)")
    print(f"Raport: {report_path} (+ .csv) · OCR: {summary['silnik_ocr'] or 'brak Tesseracta'} · exit code {stats['exit_code']}")
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


def serve(port=8000, use_llm=True, host="127.0.0.1"):
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

    def wizard_api(path, body, content_type):
        """Kreator nowego typu dokumentu: /api/wizard/{yaml,test,save}."""
        import wizard

        pdf = None
        if path == "/api/wizard/test":
            parts = _parse_multipart(content_type, body)
            spec = next((p["data"] for p in parts if p["field"] == "spec"), b"{}")
            pdf = next((p["data"] for p in parts if p["filename"] and p["field"] != "spec"), None)
            form = json.loads(spec.decode("utf-8"))
        else:
            form = json.loads(body.decode("utf-8") or "{}")
        try:
            cid, cls = wizard.build_class(form, config)
        except ValueError as exc:
            return {"errors": exc.args[0]}, 422
        out = {"id": cid, "yaml": wizard.to_yaml(cid, cls)}
        if path == "/api/wizard/save":
            saved, new_config = wizard.save_class(cid, cls)
            config.clear()
            config.update(new_config)  # nowa klasa działa od razu w zakładkach Dokument/Sprawa
            out["saved"] = os.path.relpath(saved, os.path.dirname(os.path.abspath(__file__)))
        elif path == "/api/wizard/test":
            if not pdf:
                return {"errors": ["wybierz plik PDF do testu"]}, 422
            fd, tmp = tempfile.mkstemp(suffix=".pdf", prefix="odwolania2json_wiz_")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(pdf)
                from extractor import extract_pages
                pages, info = extract_pages(tmp)
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            text = "\n".join(pages)
            tmp_config, res = wizard.test_on_text(cid, cls, text, config)
            res["provenance"] = build_provenance(res["result"], cid, {"pages": pages, "text": text}, tmp_config)
            res["info"] = _public_info(info)
            out.update(res)
        return out, 200

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send_file(os.path.join(static_dir, "index.html"), "text/html; charset=utf-8")
            elif self.path == "/api/wizard/rules":
                import wizard
                self._send_json({"rules": wizard.rule_catalog(),
                                 "classes": {k: v.get("label", k) for k, v in config.get("classes", {}).items()}})
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if self.path in ("/api/wizard/yaml", "/api/wizard/test", "/api/wizard/save"):
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_UPLOAD:
                    self._send_json({"error": f"plik za duży (limit {MAX_UPLOAD // 2**20} MB)"}, 413)
                    return
                try:
                    obj, code = wizard_api(self.path, self.rfile.read(length), self.headers.get("Content-Type"))
                    self._send_json(obj, code)
                except Exception as exc:  # noqa: BLE001
                    self._send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)
                return
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

    shown = "127.0.0.1" if host in ("0.0.0.0", "") else host
    extra = " — nasłuch na wszystkich interfejsach (np. VM z otwartym portem)" if host in ("0.0.0.0", "") else ""
    print(f"GUI: http://{shown}:{port}{extra}  (Ctrl+C, aby zatrzymać)")
    HTTPServer((host, port), Handler).serve_forever()


def main():
    ap = argparse.ArgumentParser(description="Odwołania2JSON — PDF sprawy KIO -> JSON")
    ap.add_argument("input", nargs="?", default="input")
    ap.add_argument("output", nargs="?", default="output")
    ap.add_argument("--serve", action="store_true", help="uruchom GUI")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1",
                    help="adres nasłuchu GUI (0.0.0.0 = dostęp z zewnątrz, np. VM / Docker)")
    ap.add_argument("--no-llm", action="store_true", help="tylko regexy, bez LLM-a")
    ap.add_argument("--report", default=None, help="ścieżka raportu wsadowego JSON (domyślnie <output>_raport.json)")
    ap.add_argument("--workers", type=int, default=None,
                    help=f"procesy robocze trybu wsadowego (domyślnie min({DEFAULT_WORKERS}, liczba rdzeni); "
                         "1 = jeden proces bez równoległości)")
    args = ap.parse_args()
    if args.workers is not None and args.workers < 1:
        ap.error("--workers musi być liczbą >= 1")
    if args.no_llm:
        # Wyłącza KAŻDE wywołanie LLM-a, także normalizację nazwisk do mianownika.
        os.environ["LLM_OFF"] = "1"
    if args.serve:
        serve(args.port, use_llm=not args.no_llm, host=args.host)
    elif os.path.isfile(args.input):
        config = load_config()
        out_path = args.output
        if os.path.isdir(out_path) or (not out_path.endswith(".json") and not os.path.exists(out_path)):
            os.makedirs(out_path, exist_ok=True)
            out_path = os.path.join(out_path, os.path.splitext(os.path.basename(args.input))[0] + ".json")
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        t0 = time.perf_counter()
        try:
            result, doc_type, info = process_file(args.input, config, use_llm=not args.no_llm)
        except Exception as exc:  # noqa: BLE001
            print(f"[ERR] {args.input}: {exc} -> pusty JSON {{}}")
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump({}, fh, ensure_ascii=False, indent=2)
            sys.exit(1)
        unreadable = _unreadable(args.input, info) if doc_type == "nieznany" else None
        if unreadable:
            print(f"[ERR] {args.input}: {unreadable} -> pusty JSON {{}}")
            _write_json(out_path, {}, "nieznany", config)
            sys.exit(1)
        if doc_type == "nieznany" or result is None:
            # Dokument obcy -> pusty JSON {} (tak samo jak w trybie wsadowym).
            print(f"[ODRZUCONO] {args.input}: nie udało się znaleźć typu dokumentu (typ: nieznany) -> pusty JSON {{}}")
            _write_json(out_path, {}, "nieznany", config)
            sys.exit(0)
        errors = _write_json(out_path, result, doc_type, config)
        if errors:
            print(f"[INVALID] {args.input}: walidacja JSON: {'; '.join(errors)}")
            sys.exit(1)
        print(f"[OK] {args.input} -> {out_path} ({doc_type}, ocr={info.get('ocr')}, {time.perf_counter() - t0:.2f} s)")
    elif os.path.isdir(args.input):
        stats = process_folder(args.input, args.output, use_llm=not args.no_llm, report_path=args.report,
                               workers=args.workers)
        sys.exit(stats["exit_code"])
    else:
        print(f"[ERR] brak pliku ani folderu wejściowego: {args.input}")
        sys.exit(2)


if __name__ == "__main__":
    main()
