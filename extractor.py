"""Ekstrakcja Odwołania2JSON: tekst PDF -> klasyfikacja -> regexy -> LLM (tylko pola niejednoznaczne).

Warstwy:
  1. Tekst z PDF (PyMuPDF; fallback: pdftotext; OCR Tesseract, gdy tekstu <200 znaków/stronę).
  2. Klasyfikacja typu dokumentu po słowach kluczowych z config/document_types.yaml.
  3. Deterministyczne regexy: NIP, KRS, REGON, daty, numery BZP/TED/postępowania,
     kwota, rachunki, numer transakcji, PESEL.
  4. Open-source LLM tylko do: czynnosc_powod_id/opis, tytul, nazwa, miejscowosc,
     data nagłówka — i tylko gdy regex/heurystyka nie dały wyniku.
  5. Zasada UZP: brak danej = null, nigdy nie wymyślamy. Zła wartość to FP+FN,
     null to tylko FN.
"""

import os
import re
import subprocess

import yaml

MONTHS_PL = {
    "stycznia": 1, "lutego": 2, "marca": 3, "kwietnia": 4, "maja": 5, "czerwca": 6,
    "lipca": 7, "sierpnia": 8, "września": 9, "wrzesnia": 9, "października": 10,
    "pazdziernika": 10, "listopada": 11, "grudnia": 12,
}
NAME_RE = r"[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+(?: [A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)+"
# Imiona i nazwiska w danych treningowych mają zawsze dokładnie 2 wyrazy (193/193).
NAME2_RE = r"[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+ [A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+(?:-[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)?"


def _flat(text):
    """Tekst z połączonymi wierszami — nazwiska łamane między wierszami PDF-a."""
    return re.sub(r"\s*\n\s*", " ", text or "")


def load_config(path=None):
    # Domyślnie względem katalogu aplikacji — działa niezależnie od bieżącego katalogu.
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "document_types.yaml")
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------- tekst z PDF
def _text_via_fitz(pdf_path):
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf
    doc = pymupdf.open(pdf_path)
    pages = [page.get_text().replace("\xa0", " ") for page in doc]
    return pages


def _text_via_pdftotext(pdf_path):
    out = subprocess.run(
        ["pdftotext", "-layout", pdf_path, "-"],
        capture_output=True, text=True, timeout=60,
    )
    return out.stdout.split("\f") if out.returncode == 0 else [""]


def _ocr_pages(pdf_path, pages):
    """OCR Tesseract (lang pol) dla stron prawie bez tekstu. Zwraca (pages, użyto_ocr)."""
    try:
        import pytesseract
        from PIL import Image
        import pymupdf
    except ImportError:
        return pages, False
    tess_win = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if os.path.exists(tess_win):
        pytesseract.pytesseract.tesseract_cmd = tess_win
    try:
        doc = pymupdf.open(pdf_path)
    except Exception:
        return pages, False
    import hashlib

    used = False
    new_pages = list(pages)
    cache_dir = os.path.join(os.path.dirname(__file__), ".cache_ocr")
    os.makedirs(cache_dir, exist_ok=True)
    mtime = os.path.getmtime(pdf_path) if os.path.exists(pdf_path) else 0
    try:  # drugi klucz cache: treść pliku (ten sam skan wgrany w GUI / przeniesiony folder)
        with open(pdf_path, "rb") as fh:
            content_key = hashlib.sha256(fh.read()).hexdigest()[:16]
    except OSError:
        content_key = None

    def _store(path, txt):
        if path and not os.path.isfile(path):
            try:
                with open(path, "w", encoding="utf-8") as cf:
                    cf.write(txt)
            except Exception:
                pass

    for i, page in enumerate(doc):
        if len((pages[i] if i < len(pages) else "").strip()) >= 200:
            continue
        cache_key = hashlib.sha256(f"{os.path.abspath(pdf_path)}_{mtime}_{i}".encode("utf-8")).hexdigest()[:16]
        cache_file = os.path.join(cache_dir, f"{cache_key}.txt")
        content_file = os.path.join(cache_dir, f"c_{content_key}_{i}.txt") if content_key else None
        cached = None
        for path in (content_file, cache_file):  # klucz ścieżki = starszy format, tylko odczyt
            if path and os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8") as cf:
                        cached = cf.read()
                    break
                except Exception:
                    pass
        if cached is not None:
            new_pages[i] = cached
            used = True
            _store(content_file, cached)
            continue
        try:
            pix = page.get_pixmap(dpi=300)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            txt = pytesseract.image_to_string(img, lang="pol")
            _store(content_file or cache_file, txt)
            new_pages[i] = txt
            used = True
        except Exception:
            continue
    return new_pages, used


def extract_pages(pdf_path):
    """Zwraca (lista tekstów stron, info dict). Nigdy nie rzuca wyjątku."""
    info = {"ocr": False, "pages": 0, "chars": 0}
    try:
        pages = _text_via_fitz(pdf_path)
    except Exception:
        try:
            pages = _text_via_pdftotext(pdf_path)
        except Exception:
            return [], {**info, "error": "nie można odczytać PDF"}
    info["pages"] = len(pages)
    info["chars"] = sum(len(p) for p in pages)
    avg = info["chars"] / max(1, info["pages"])
    if avg < 200:
        pages, used = _ocr_pages(pdf_path, pages)
        info["ocr"] = used
        if not used:
            info["needs_ocr"] = True
        info["chars"] = sum(len(p) for p in pages)
    return pages, info


def extract_text(pdf_path):
    """Zwraca (tekst_łączny, info dict). Nigdy nie rzuca wyjątku."""
    pages, info = extract_pages(pdf_path)
    return "\n".join(pages), info


# ---------------------------------------------------------------- klasyfikacja
# Wagi: przelew ma kilkanaście wariantów bankowych, więc nie polegamy na
# nagłówku banku — liczą się generyczne sygnały (rachunki, kwoty, etykiety).
CLASSIFY_WEIGHTS = {
    "odwolanie": [("zarzut nr", 5), ("odwołujący:", 3), ("prezes krajowej izby odwoławczej", 2),
                  ("odwołanie", 2)],
    "pelnomocnictwo": [("pełnomocnictwo", 4), ("mocodawc", 3),
                        ("pełnomocnik jest umocowany", 2), ("udzielam pełnomocnictwa", 2)],
    "przelew": [("potwierdzenie wykonania przelewu", 5), ("polecenie przelewu", 5),
                ("potwierdzenie przelewu", 4), ("dowód wykonania przelewu", 4),
                ("potwierdzenie transakcji", 5), ("przelew elixir", 5), ("przelew krajowy", 5),
                ("szczegóły zlecenia", 4), ("typ zlecenia", 4), ("typ przelewu", 4),
                ("typ transakcji", 4), ("kwota przelewu", 4),
                ("z rachunku", 3), ("na rachunek", 3), ("dane nadawcy", 3), ("dane odbiorcy", 3),
                ("dane kontrahenta", 3), ("rachunek kontrahenta", 4),
                ("rachunek odbiorcy", 3), ("rachunek zleceniodawcy", 4),
                ("rachunek obciążany", 3), ("zleceniodawca", 3),
                ("tytuł przelewu", 3), ("data operacji", 2), ("data wykonania", 3),
                ("data księgowania", 2), ("numer referencyjny", 2), ("kwota operacji", 2),
                ("przelew", 2)],
}


def classify(text, config, filename=""):
    first = text[:4000].lower()
    first_normalized = re.sub(r"(?<=\w)\s+(?=\w)", "", first)
    first_ws = re.sub(r"\s+", " ", first)
    scores = {}
    classes = config.get("classes", {})
    min_score = config.get("classify_min_score", 4)

    for name, cls in classes.items():
        w_kws = cls.get("weighted_keywords") or CLASSIFY_WEIGHTS.get(name, [])
        s = 0
        for kw, w in w_kws:
            if kw in first or kw in first_normalized:
                s += w
        for kw in cls.get("keywords", []):
            kl = kw.lower()
            if kl in first or kl in first_normalized:
                s += 1
        # Sygnały dokumentów obcych (np. orzeczenia KIO) — odejmują punkty (config).
        for kw, w in cls.get("negative_keywords") or []:
            kl = kw.lower()
            if kl in first_ws or kl in first_normalized:
                s -= w
        scores[name] = s

    if not scores:
        return "nieznany"

    best = max(scores, key=lambda k: scores[k])
    if scores[best] < min_score:
        return "nieznany"
    return best


# ---------------------------------------------------------------- pomocnicze
def _digits(s):
    return re.sub(r"\D", "", s or "")


def _find(pattern, text, group=1, flags=re.IGNORECASE):
    m = re.search(pattern, text, flags)
    if not m:
        return None
    try:
        return m.group(group).strip()
    except IndexError:
        return m.group(0).strip()


def _norm_space(s):
    return re.sub(r"\s+", " ", s or "").strip()


def _iso_date(d, m, y):
    try:
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    except (TypeError, ValueError):
        return None


def _parse_pl_date(text):
    """'20 marca 2026' / '1 kwietnia 2026' -> ISO. Zwraca None, gdy brak."""
    m = re.search(
        r"(\d{1,2})\s+(stycznia|lutego|marca|kwietnia|maja|czerwca|lipca|sierpnia|września|wrzesnia|października|pazdziernika|listopada|grudnia)\s+(\d{4})",
        text, re.IGNORECASE,
    )
    if m:
        return _iso_date(m.group(1), MONTHS_PL[m.group(2).lower()], m.group(3))
    m = re.search(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})", text)
    if m:
        return _iso_date(m.group(1), m.group(2), m.group(3))
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def _header_date_place(text):
    """Data i miejscowość z 1. strony: 'Kraków, 02.07.2026' / 'Łódź, 1 kwietnia 2026' / 'X, dnia DD.MM.RRRR'."""
    p1 = text.split("\x0c")[0]
    # Miejscowość: do 3 członów, każdy może mieć łącznik (Bielsko-Biała, Nowy Dwór Mazowiecki).
    word = r"[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+(?:-[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)?"
    pat_line = re.compile(
        r"^\s*(" + word + r"(?:[ \t]+" + word + r"){0,2})\s*,\s*(?:dnia\s+)?(.+)",
        re.MULTILINE,
    )
    for m in pat_line.finditer(p1):
        place = m.group(1).strip()
        date = _parse_pl_date(m.group(2))
        if date and place:
            return date, place
    # Fallback tylko dla linii 1-3 na skanach: 'Łódź 05.06.2026r.'
    for l in p1.splitlines()[:3]:
        m_top = re.match(r"^\s*(" + word + r")\s+(\d{1,2}[./]\d{1,2}[./]\d{4}\s*r?\.?)", l.strip())
        if m_top:
            place = m_top.group(1).strip()
            date = _parse_pl_date(m_top.group(2))
            if date and place and place.lower() not in ("prezes", "zamawiający", "odwołujący", "kancelaria"):
                return date, place
    return None, None


def _party_block(text, label, next_labels=("Zamawiający", "ODWOŁANIE", "dotyczy", "Wykonawca wybrany", "Uczestnik")):
    """Blok tekstu po etykiecie strony postępowania, np. 'Odwołujący:'."""
    idx = text.find(label)
    if idx < 0:
        return ""
    rest = text[idx + len(label):]
    cut = len(rest)
    for nl in next_labels:
        j = rest.find(nl)
        if 0 <= j < cut:
            cut = j
    return rest[:cut]


def _ids_from_block(block):
    krs = _find(r"KRS[:\s]*(\d{10})", block)
    nip_raw = _find(r"NIP[:\s]*([\d\s\-]{9,14})", block)
    nip = _digits(nip_raw)[:10] if nip_raw else None
    if nip and len(nip) != 10:
        nip = None
    regon_m = re.search(r"REGON[:\s]*(\d{9}(?:\d{5})?)\b", block)
    regon = regon_m.group(1) if regon_m else None
    return krs, nip, regon


def is_inflected_name(name):
    """Zwraca True, jeśli imię lub nazwisko wykazuje cechy formy odmienionej (nie-mianownik)."""
    if not name or not isinstance(name, str):
        return False
    parts = name.strip().split()
    if len(parts) < 2:
        return False
    first, last = parts[0], parts[-1]
    f_low, l_low = first.lower(), last.lower()

    if l_low.endswith(("skiego", "ckiego", "dzkiego", "skiemu", "ckiemu", "dzkiemu", "skim", "ckim", "dzkim", "skiej", "ckiej", "dzkiej", "ską", "cką", "dzką")):
        return True
    if f_low.endswith("owi") or f_low.endswith("em") or f_low.endswith("łem") or f_low.endswith("ę"):
        return True
    return False


def normalize_person_name(name, full_doc_text=""):
    """Zasada UZP (15:55): wszystkie imie_nazwisko w mianowniku.
    1. Bierz z miejsca, gdzie występuje w mianowniku (blok podpisu, 'Pełnomocnik: Jan Kowalski').
    2. Form odmienionych nie kopiuj bez normalizacji.
    3. Jak brak mianownika w tekście -> LLM (Mistral). Jak się nie uda -> zwróć None (null).
    """
    if not name or not isinstance(name, str):
        return None
    name = " ".join(name.strip().split())
    if not is_inflected_name(name):
        return name

    # 1. Szukaj mianownika w dokumencie (blok podpisu, nagłówek, lista)
    if full_doc_text:
        parts = name.split()
        first, last = parts[0], parts[-1]
        candidates = re.findall(NAME2_RE, full_doc_text)
        for cand in candidates:
            cand_clean = " ".join(cand.split())
            if not is_inflected_name(cand_clean):
                c_parts = cand_clean.split()
                if len(c_parts) == 2:
                    if c_parts[1][:4].lower() == last[:4].lower() and c_parts[0][:3].lower() == first[:3].lower():
                        return cand_clean

    # 2. Użyj LLM (Mistral API)
    try:
        from llm import normalize_name_nominative_llm
        norm = normalize_name_nominative_llm(name)
        if norm and not is_inflected_name(norm):
            return norm
    except Exception:
        pass

    # 3. Jeśli nie udało się sprowadzić do mianownika -> null (nie kopiuj formy odmienionej)
    return None


def _first_name_line(block):
    m_pod = re.search(r"pod\s+(?:firmą|nazwą)[:\s]*\n\s*([^\n]+)", block, re.IGNORECASE)
    if m_pod:
        return m_pod.group(1).strip()
    for line in block.splitlines():
        line = line.strip().lstrip(":-–— ").strip()
        if line and not re.fullmatch(r"[\W_]+", line) and not any(
            line.lower().startswith(x)
            for x in ("ul.", "tel", "e-mail", "kontakt", "adres", "lider", "partner", "krs", "nip", "regon")
        ):
            return line
    return None


CZYNNOSC_RULES = [
    (10, [
        r"zaniecha(?:ł|nia|niu|wszy)\s+unieważnieni",
        r"nie\s+unieważni",
    ]),
    (7, [
        r"zaniecha(?:ł|nia|niu|wszy)\s+odrzuceni",
        r"nie\s+odrzuci(?:ł|ła|ło|li)",
    ]),
    (8, [
        r"zaniecha(?:ł|nia|niu|wszy)\s+wykluczeni",
        r"nie\s+wykluczy(?:ł|ła|ło|li)",
    ]),
    (9, [
        r"unieważni(?:i)?(?:ł|ła|ło|li)\s+postępowani",
        r"unieważnieni(?:u|a|e)\s+postępowani",
        r"decyzj[aei]\s+o\s+unieważnieni",
    ]),
    (1, [
        r"odrzuce(?:niu|nia|nie)\s+ofert",
        r"odrzuci(?:ł|ła|ło|li)\s+ofert",
        r"decyzj[aei]\s+o\s+odrzuceni",
    ]),
    (3, [
        r"wykluczeni(?:u|a|e)\s+(?:z\s+postępowania|odwołującego|wykonawcy)",
        r"wykluczy(?:ł|ła|ło|li)\s+(?:z\s+postępowania|odwołującego|wykonawc)",
        r"decyzj[aei]\s+o\s+wykluczeni",
    ]),
    (2, [
        r"wybor(?:ze|u|e)\s+(?:jako\s+najkorzystniejszej\s+)?ofert",
        r"wybor(?:ze|u|e)\s+ofert",
        r"wybra(?:ł|ła|ło|li)\s+ofert",
        r"ogłosi(?:ł|enie|enia)\s+wybór",
        r"ogłoszeni[aeu]\s+wyboru",
        r"dokona(?:ł|nia)\s+wyboru",
    ]),
    (6, [
        r"odpowiedzi\s+i\s+wyjaśnie",
        r"udzieli(?:ł|enia)\s+(?:odpowiedzi|wyjaśnie)",
        r"wyjaśnie(?:ń|nia)\s+treści\s+swz",
        r"wyjaśnie(?:ń|nia)\s+swz",
        r"treści\s+wyjaśnie",
        r"wyjaśnie(?:ń|nia)\s+do\s+swz",
    ]),
    (5, [
        r"zmodyfikowa(?:ł|nia|niu)\s+treś",
        r"modyfikacj(?:i|a|ę)\s+treści\s+swz",
        r"modyfikacj(?:i|a|ę)\s+swz",
        r"zmian(?:a|y|ie)\s+treści\s+swz",
    ]),
    (12, [
        r"projektowan(?:e|ych)\s+postanowie",
        r"wzor(?:zec|u|zec)\s+umowy",
        r"postanowie(?:ń|nia)\s+umowy",
    ]),
    (11, [
        r"opis(?:ania|u|ie|em)?\s+przedmiotu\s+zamówienia",
        r"udostępni(?:ł|enia)\s+opis\s+przedmiotu",
        r"\bopz\b",
    ]),
    (4, [
        r"opublikowa(?:ł|nia)\s+specyfikacj",
        r"udostępni(?:ł|enia)\s+specyfikacj",
        r"publikacj(?:a|i|ę)\s+specyfikacj",
        r"sformułowani(?:a|e)\s+postanowień\s+swz",
        r"postanowień\s+swz",
        r"specyfikacj(?:i|a|ę)\s+warunków\s+zamówienia",
    ]),
]


# ---------------------------------------------------------------- odwołanie
def _czynnosc(text, config):
    """Powód TYLKO z pierwszego zarzutu lub zaskarżonej czynności przed Zarzutem 1."""
    # 0. Wprost pod 'wnoszę odwołanie wobec: ... 1. ...'
    m_wn = re.search(r"wnoszę\s+odwołanie\s+wobec:?[\s\S]{0,500}?(?:^|\n)\s*[1I|l]\.?\s*([^\n;]+)", text, re.IGNORECASE)
    if m_wn:
        wn_text = m_wn.group(1).lower()
        for cid, patterns in CZYNNOSC_RULES:
            for pat in patterns:
                if re.search(pat, wn_text):
                    return cid, config["czynnosc_powod"].get(cid)
        wn_text = m_wn.group(1).lower()
        for cid, patterns in CZYNNOSC_RULES:
            for pat in patterns:
                if re.search(pat, wn_text):
                    return cid, config["czynnosc_powod"].get(cid)

    # 1. Jeśli w Zarzucie 1 jest myślnik: 'Zarzut nr 1 – ...' lub 'Zarzut nr 1 - ...'
    m_dash = re.search(r"Zarzut\s+nr\s+1\s*[–—-]\s*([^\n;]+)", text, re.IGNORECASE)
    if m_dash:
        dash_text = m_dash.group(1).lower()
        for cid, patterns in CZYNNOSC_RULES:
            for pat in patterns:
                if re.search(pat, dash_text):
                    return cid, config["czynnosc_powod"].get(cid)

    # 2. Szukaj w pierwszym zdaniu 'Zamawiający w dniu ...' (do pierwszej kropki)
    m = re.search(r"Zamawiający\s+w\s+dniu[^\n.]+?(?:roku|r\.)\s*([^\n.]*?\.)", text, re.DOTALL | re.IGNORECASE)
    sentence = " ".join(m.group(0).split()).lower() if m else ""
    if sentence:
        for cid, patterns in CZYNNOSC_RULES:
            for pat in patterns:
                if re.search(pat, sentence):
                    return cid, config["czynnosc_powod"].get(cid)

    # 3. Jeśli nie ma kropki, szukaj w całym fragmencie przed Zarzutem 1
    m_all = re.search(r"Zamawiający\s+w\s+dniu.*?(?=Zarzut|\Z)", text, re.DOTALL | re.IGNORECASE)
    if m_all:
        sent_all = " ".join(m_all.group(0).split()).lower()
        for cid, patterns in CZYNNOSC_RULES:
            for pat in patterns:
                if re.search(pat, sent_all):
                    return cid, config["czynnosc_powod"].get(cid)

    # 4. Cały Zarzut nr 1
    m1 = re.search(r"Zarzut\s+nr\s+1[^\n.]*[\n.]*?(.*?)(?=Zarzut\s+nr\s+2|\n\s*\n\s*\n|$)", text, re.IGNORECASE | re.DOTALL)
    z1 = " ".join(m1.group(0).split()).lower() if m1 else ""
    if z1:
        for cid, patterns in CZYNNOSC_RULES:
            for pat in patterns:
                if re.search(pat, z1):
                    return cid, config["czynnosc_powod"].get(cid)

    # 5. Fallback na keywords z config
    scopes = []
    if m1:
        scopes.append(m1.group(0))
    if m_all:
        scopes.append(m_all.group(0))
    if m_wn:
        scopes.append(m_wn.group(0))
    for scope in scopes:
        low = scope.lower()
        for *keys, cid in config.get("czynnosc_keywords", []):
            if any(k.lower() in low for k in keys):
                return cid, config["czynnosc_powod"].get(cid)

    return None, None


# Numer postępowania: token bez spacji (spacje tolerowane tylko wokół "/", np. OCR "ZP/ 134/2026").
# Dawny wzorzec z \s w klasie znaków doklejał kolejne słowa ("ZP/45/2026dotyczy"),
# gdy po numerze nie było przecinka.
NR_POST_LABELS = r"(?:n[rt]\.?\s+referencyjny(?:\s+sprawy)?|numer\s+postępowania|n[rt]\.?\s+postępowania|znak\s+sprawy)"
NR_POST_TOKEN = r"[A-Za-z0-9]+(?:(?:[._\-]|\s*/\s*)[A-Za-z0-9]+)*"


def _numer_postepowania(text, labels=NR_POST_LABELS):
    m = re.search(labels + r"[:\s\.]+(" + NR_POST_TOKEN + ")", _flat(text), re.IGNORECASE)
    if not m:
        return None
    nr = re.sub(r"\s+", "", m.group(1))
    return nr if 3 <= len(nr) <= 40 else None


def _czynnosc_dzien(text):
    m = re.search(
        r"(?:w\s+dniu|pismem\s+z)\s+(\d{1,2})\s+(stycznia|lutego|marca|kwietnia|maja|czerwca|lipca|sierpnia|września|wrzesnia|października|pazdziernika|listopada|grudnia)\s+(\d{4})",
        text, re.IGNORECASE,
    )
    if m:
        return _iso_date(m.group(1), MONTHS_PL[m.group(2).lower()], m.group(3))
    m2 = re.search(r"(?:w\s+dniu|pismem\s+z)\s+(\d{1,2})[./\-](\d{1,2})[./\-](\d{4})", text, re.IGNORECASE)
    if m2:
        return _iso_date(m2.group(1), m2.group(2), m2.group(3))
    m3 = re.search(r"zaskarżonej\s+czynności\s+została\s+(?:przekazana|opublikowana)\s+w\s+dniu\s+(\d{1,2})[./\-](\d{1,2})[./\-](\d{4})", text, re.IGNORECASE)
    if m3:
        return _iso_date(m3.group(1), m3.group(2), m3.group(3))
    return None


def extract_odwolanie(text, config, llm_fields=None):
    llm_fields = llm_fields or {}
    date, place = _header_date_place(text)
    cid, cdesc = _czynnosc(text, config)
    if cid is None and llm_fields.get("czynnosc_powod_id") is not None:
        try:
            cid = int(llm_fields["czynnosc_powod_id"])
            cdesc = config["czynnosc_powod"].get(cid)
        except (TypeError, ValueError):
            cid, cdesc = None, None
        if cdesc is None:  # id spoza słownika 1-12 -> null, nie zgadujemy
            cid = None

    odw_block = _party_block(text, "Odwołujący:")
    m_kons = re.search(r"1\)\s*(?:Lider\s+Konsorcjum\s*:?\s*)?(?:\n\s*)?([^\n,\(]+)", odw_block, re.IGNORECASE)
    if m_kons and "konsorcjum" in odw_block.lower():
        odw_name = m_kons.group(1).strip()
    else:
        odw_name = _first_name_line(odw_block)
    krs, nip, regon = _ids_from_block(odw_block)

    # Pelnomocnik
    peln_match = re.search(
        r"(?:reprezentowan[a-ząćęłńóśźż/]*\s+przez(?:\s+pełnomocnika)?[:\s\.]*)?(?:radc[ay]\s+prawn[ya]|adwokat|r\.\s*pr\.|adw\.|mec\.)\s*(" + NAME2_RE + ")",
        _flat(odw_block), re.IGNORECASE,
    )
    if not peln_match:
        peln_match = re.search(
            r"reprezentowan[a-ząćęłńóśźż/]*\s+przez(?:\s+pełnomocnika)?[:\s\.]*\s*(" + NAME2_RE + ")",
            _flat(odw_block), re.IGNORECASE,
        )
    peln_name = peln_match.group(1) if peln_match else llm_fields.get("pelnomocnik_imie_nazwisko")
    if peln_name:
        peln_name = normalize_person_name(peln_name, text)

    # Kancelaria
    kanc = None
    m_dot_nl = re.search(r"adres do korespondencji:\s*[.]\s*\n\s*([^\n]+)", odw_block, re.IGNORECASE)
    if m_dot_nl:
        kanc = m_dot_nl.group(1).strip()
    else:
        m_k = re.search(r"adres do korespondencji:\s*([^,]+)", odw_block, re.IGNORECASE)
        if m_k:
            kanc = m_k.group(1).strip()
    if not kanc:
        kanc = _find(r"(Kancelaria\s+(?:Radcy\s+Prawnego|Adwokacka|Prawna)[^\n,]+)", odw_block)
    if kanc:
        kanc = re.split(r",\s*ul\.|\s+ul\.", kanc)[0].strip()
        kanc = _norm_space(kanc)
    else:
        kanc = None

    # Mail
    mail = None
    m_m1 = re.search(r"[\w\.\-]+@[\w\.\-]+\.[a-z]{2,}", odw_block)
    if m_m1:
        mail = m_m1.group(0)
    else:
        m_m2 = re.search(r"e-mail[:\s]*([^\n,;]+)", odw_block, re.IGNORECASE)
        if m_m2:
            raw_m = re.sub(r"\s+", "", m_m2.group(1).strip())
            raw_m = re.sub(r"\([aAoO]?[QatAT]+\)?", "@", raw_m)
            raw_m = re.sub(r"\(a", "@", raw_m)
            raw_m = re.sub(r"(?<=[a-z0-9])Q(?=[a-z0-9])", "@", raw_m)
            if "@" not in raw_m and peln_name and len(peln_name.split()) >= 2:
                parts = peln_name.lower().split()
                prefix = f"{parts[0][0]}.{parts[1]}"
                if raw_m.startswith(prefix + "a"):
                    raw_m = prefix + "@" + raw_m[len(prefix)+1:]
            raw_m = re.sub(r"\.exam[a-złę]+$", ".example", raw_m)
            if "@" in raw_m:
                mail = raw_m
    przez = 1 if peln_name else 0

    m_zam_hdr = re.search(r"(?:^|\n)\s*(?:1\)\s*)?Zamawiający\b(?:\s*[:\(]|\s*–|\s*-\s*|\s*\n)", text)
    if m_zam_hdr:
        zam_block = _party_block(text[m_zam_hdr.start():], "Zamawiający", next_labels=("Odwołujący", "ODWOŁANIE", "dotyczy"))
    else:
        zam_block = ""
    if not zam_block.strip() or len(zam_block.strip()) < 10:
        header_part = re.split(r"\n\s*(?:Dotyczy:|postępowania\s+o\s+udzielenie)", text, flags=re.IGNORECASE)[0]
        m_nips = list(re.finditer(r"NIP[:\s]*([\d\s\-]{9,14})", header_part))
        if len(m_nips) >= 2:
            zam_block = header_part[m_nips[0].end():].strip()
        else:
            m_zb = re.search(r"(?:Odwołujący:.*?\n\s*\n\s*)([A-ZĄĆĘŁŃÓŚŹŻ0-9][^\n]+(?:\n[^\n]+)*?)(?=\n\s*(?:Dotyczy:|postępowania\s+o\s+udzielenie))", text, re.DOTALL | re.IGNORECASE)
            if m_zb:
                zam_block = m_zb.group(1)
    zamawiajacy = _parse_zamawiajacy(zam_block)

    # Tytuł zamówienia
    tytul = None
    m_tyt1 = re.search(r"pn\.\s*[„\",\.\'\s]*([^\n,\"]+?(?:\n[^\n,\"]+?)*?)[”\"']", text, re.IGNORECASE)
    if m_tyt1:
        tytul = _norm_space(m_tyt1.group(1)).strip(' „"\'.,;')
    if not tytul:
        m_tyt2 = re.search(r"pn\.\s*[„\",\.\'\s]*([^\n,\"]+?(?:\n[^\n,\"]+?)*?)(?=,\s*nr|\s+nr\s+ref|,\s*ogłoszenie|\Z)", text, re.IGNORECASE)
        if m_tyt2:
            tytul = _norm_space(m_tyt2.group(1)).strip(' „"\'.,;')
    if not tytul:
        tytul = _find(r"pn\.\s*[„\"](.+?)[”\"]", text, flags=re.DOTALL)
        tytul = _norm_space(tytul) if tytul else None
    if not tytul and llm_fields.get("tytul") and _grounded(llm_fields["tytul"], text):
        # Tytuł z LLM-a tylko, gdy jego słowa faktycznie są w dokumencie (bez halucynacji).
        tytul = _norm_space(str(llm_fields["tytul"]))

    # BZP i TED - szukaj przed częścią zasadniczą (UZASADNIENIE / Zarzut nr 1), by unikać starych cytowań
    m_split = re.search(r"\n\s*(?:UZASADNIENIE|Zarzut\s+nr\s+1)", text, re.IGNORECASE)
    scope = text[:m_split.start()] if m_split else text[:4000]
    flat_pre = _flat(scope)

    m_bzp = re.search(r"(\d{4}/BZP\s*\d+(?:/\d+)?)", flat_pre, re.IGNORECASE)
    bzp = re.sub(r"\s+", "", m_bzp.group(1)) if m_bzp else None

    m_ted1 = re.search(r"(?:numer ogłoszenia TED|TED)[:\s]*(\d{6}-\d{4})", flat_pre, re.IGNORECASE)
    m_ted2 = re.search(r"(\d{4})/[S5]\d*\s*(?:\d+[-–—\s]+)?(\d{6})", flat_pre, re.IGNORECASE)
    if m_ted1:
        ted = m_ted1.group(1)
    elif m_ted2:
        ted = f"{m_ted2.group(2)}-{m_ted2.group(1)}"
    else:
        m_ted3 = re.search(r"\b(\d{6}-\d{4})\b", flat_pre)
        ted = m_ted3.group(1) if m_ted3 else None

    nr_post = _numer_postepowania(text)

    return {
        "OdwolanieKIO": {
            "data": date,
            "czynnosc_dzien": _czynnosc_dzien(text),
            "czynnosc_powod_id": cid,
            "czynnosc_opis": cdesc,
            "miejscowosc": place,
        },
        "Odwolujacy": [{
            "krs": krs,
            "nazwa": odw_name or llm_fields.get("odwolujacy_nazwa"),
            "nip": nip,
            "regon": regon,
            "przez-pelnomocnika": przez,
            "pelnomocnik": {
                "imie_nazwisko": peln_name,
                "nazwa": kanc if peln_name else None,
                "mail": mail if peln_name else None,
            },
        }],
        "Zamawiajacy": zamawiajacy,
        "ZamowieniePubliczne": {
            "numer_bzp": bzp,
            "numer_postepowania": nr_post,
            "numer_ted": ted,
            "tytul": tytul,
        },
    }


def _parse_zamawiajacy(block):
    """Jeden lub dwóch zamawiających (wspólne postępowanie: '1) ... Lider, prowadzący postępowanie')."""
    parts = re.split(r"\n\s*\d+\)\s*(?:Zamawiający)?", block)
    entries = []
    if len(parts) > 1:
        for i, part in enumerate(parts[1:], start=1):
            header = part[:200].lower()
            prowadzacy = 1 if ("prowadz" in header or "lider" in header) else 0
            name = _first_name_line(part)
            krs, nip, regon = _ids_from_block(part)
            entries.append({"krs": krs, "nazwa": name, "nip": nip, "regon": regon,
                            "prowadzacy_postepowanie": prowadzacy})
        return entries
    name = _first_name_line(block)
    krs, nip, regon = _ids_from_block(block)
    return [{"krs": krs, "nazwa": name, "nip": nip, "regon": regon,
             "prowadzacy_postepowanie": None}]


# ---------------------------------------------------------------- pełnomocnictwo
def parse_signers_block(text_block):
    """Parsuje listę podpisujących mocodawcy pod liniami kropek, ignorując 'Za <Firma>'."""
    text_clean = re.split(r"Pełnomocnictwo\s+przyjmuj[ęe]", text_block, flags=re.IGNORECASE)[0]
    lines = [l.strip() for l in text_clean.splitlines() if l.strip()]

    signers = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if re.match(r"^\.{5,}$", line):
            j = i + 1
            while j < len(lines) and (
                lines[j].startswith("Za ") or lines[j].startswith("W imieniu") or re.match(r"^\.{5,}$", lines[j])
            ):
                j += 1
            if j < len(lines):
                cand = lines[j]
                m = re.match(r"^([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+ [A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+(?:-[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)?)$", cand)
                if m:
                    name = normalize_person_name(m.group(1), text_block)
                    if name and name not in signers:
                        signers.append(name)
                i = j
        i += 1
    return [{"imie_nazwisko": s} for s in signers]


DISCARD_TITLES = {"prezes", "członek", "wiceprezes", "prokurent", "dyrektor", "zarządu", "lider", "partner", "zarząd"}


def extract_pelnomocnictwo(text, config, llm_fields=None):
    flat = _flat(text)

    # Konsorcjum: Wykonawcy wspólnie ubiegający się o udzielenie zamówienia (Konsorcjum): 1) ... ; 2) ...
    m_kons = re.search(
        r"Wykonawcy\s+wspólnie\s+ubiegający\s+się[^\n:]*:\s*1\)\s*([^;]+?)(?=\s+2\)|\n\s*2\))\s*2\)\s*([^;]+?)(?=\s*\(dalej|\s*reprezentowani|\s*udzielają)",
        flat, re.IGNORECASE,
    )
    if not m_kons:
        m_kons = re.search(
            r"Wykonawcy\s+wspólnie\s+ubiegający\s+się[^\n:]*:\s*1\)\s*([^;]+);\s*2\)\s*([^;]+?)(?=\s*\(dalej|\s*reprezentowani|\s*udzielają)",
            flat, re.IGNORECASE,
        )
    if m_kons:
        part1 = m_kons.group(1)
        part2 = m_kons.group(2)

        # "or None": blok zaczynający się od ", ul." dawał pusty string zamiast null.
        nazwa1 = _norm_space(re.split(r",\s*ul\.|\s+z\s+siedzibą", part1)[0]) or None
        nazwa2 = _norm_space(re.split(r",\s*ul\.|\s+z\s+siedzibą", part2)[0]) or None

        krs1, nip1, regon1 = _ids_from_block(part1)
        krs2, nip2, regon2 = _ids_from_block(part2)

        m_sig1 = re.search(r"W\s+imieniu\s+" + re.escape(nazwa1[:10]) + r".*?(?=W\s+imieniu|Pełnomocnictwo\s+przyjmuj|\Z)", text, re.DOTALL | re.IGNORECASE) if nazwa1 else None
        m_sig2 = re.search(r"W\s+imieniu\s+" + re.escape(nazwa2[:10]) + r".*?(?=W\s+imieniu|Pełnomocnictwo\s+przyjmuj|\Z)", text, re.DOTALL | re.IGNORECASE) if nazwa2 else None

        podp1 = parse_signers_block(m_sig1.group(0)) if m_sig1 else []
        podp2 = parse_signers_block(m_sig2.group(0)) if m_sig2 else []

        if not podp1:
            p1_clean = re.sub(r'([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)\s*[\.,]\s*([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)', r'\1 \2', part1)
            m_rep1 = re.search(r"(?:reprezentowan|entowan)[a-ząćęłńóśźż/]*\s*[_–—\s]*\s*przez[:\s\.]+\s*([^\(\)]+)", p1_clean, re.IGNORECASE)
            if m_rep1:
                names1 = [n for n in re.findall(NAME2_RE, m_rep1.group(1)) if not any(w.lower() in DISCARD_TITLES for w in n.split())]
                podp1 = [{"imie_nazwisko": normalize_person_name(n, text)} for n in names1 if normalize_person_name(n, text)]
        if not podp2:
            p2_clean = re.sub(r'([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)\s*[\.,]\s*([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)', r'\1 \2', part2)
            m_rep2 = re.search(r"(?:reprezentowan|entowan)[a-ząćęłńóśźż/]*\s*[_–—\s]*\s*przez[:\s\.]+\s*([^\(\)]+)", p2_clean, re.IGNORECASE)
            if m_rep2:
                names2 = [n for n in re.findall(NAME2_RE, m_rep2.group(1)) if not any(w.lower() in DISCARD_TITLES for w in n.split())]
                podp2 = [{"imie_nazwisko": normalize_person_name(n, text)} for n in names2 if normalize_person_name(n, text)]

        mocodawcy = [
            {
                "data": {"imie_nazwisko": None, "krs": krs1, "nazwa": nazwa1, "nip": nip1, "typ": "osoba_prawna"},
                "podpisujacy": podp1,
            },
            {
                "data": {"imie_nazwisko": None, "krs": krs2, "nazwa": nazwa2, "nip": nip2, "typ": "osoba_prawna"},
                "podpisujacy": podp2,
            },
        ]
    else:
        # Pojedynczy mocodawca
        fiz = re.search(r"Ja,\s*niżej podpisany(?:/a)?\s+(" + NAME2_RE + ")", text)
        if fiz:
            imie = normalize_person_name(fiz.group(1), text)
            firma_m = re.search(r"prowadz[ąa]cy działalność gospodarczą pod firmą\s+(.+?)(?=,\s*ul\.|\s+NIP)", flat, re.IGNORECASE)
            firma = firma_m.group(1).strip() if firma_m else None
            krs, nip, regon = _ids_from_block(text)
            mocodawcy = [{
                "data": {
                    "imie_nazwisko": imie,
                    "krs": None,
                    "nazwa": _norm_space(firma) if firma else None,
                    "nip": nip,
                    "typ": "osoba_fizyczna",
                },
                "podpisujacy": parse_signers_block(text) or ([{"imie_nazwisko": imie}] if imie else []),
            }]
        else:
            # Osoba prawna
            krs, nip, regon = _ids_from_block(text)
            nazwa = None
            m_post = re.search(r"PEŁNOMOCNICTWO\s*\n\s*(?:(?:1\)|-\s*)?\s*)([A-ZĄĆĘŁŃÓŚŹŻ0-9][^\n,]+?)\s+z\s+siedzibą", text, re.IGNORECASE)
            if m_post:
                nazwa = m_post.group(1).strip()
            if not nazwa:
                m1 = re.search(r"Mocodawc[ąa] jest\s+([^\n,]+)", text, re.IGNORECASE)
                if m1:
                    nazwa = m1.group(1).strip()
            if not nazwa:
                m2 = re.search(r"MOCODAWCA\s*\n\s*([^\n,]+)", text, re.IGNORECASE)
                if m2:
                    nazwa = m2.group(1).strip()
            if not nazwa:
                m3 = re.search(r"Działając\s+w\s+imieniu\s+([^\n,(]+)", text, re.IGNORECASE)
                if m3:
                    nazwa = m3.group(1).strip()
            if not nazwa:
                lines = [l.strip() for l in text.splitlines() if l.strip()]
                for l in lines[:5]:
                    if re.search(r"(?:sp\.\s*z\s*o\.o\.|s\.a\.|sp\.\s*k\.|sp\.\s*j\.|spółka|spółki)", l, re.IGNORECASE):
                        nazwa = l
                        break
            if nazwa:
                nazwa = re.split(r"\s+z\s+siedzibą|\s*\(KRS|\s*,\s*ul\.", nazwa, flags=re.IGNORECASE)[0].strip()

            podpisujacy = parse_signers_block(text)
            if not podpisujacy:
                flat_rep = re.sub(r'([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)\s*[\.,]\s*([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)', r'\1 \2', flat)
                m_rep = re.search(r"(?:reprezentowan|entowan)[a-ząćęłńóśźż/]*\s*[_–—\s]*\s*przez[:\s\.]+\s*([^\(\)]+?)(?=\(dalej|niniejszym|udzielaj|1\.|\Z)", flat_rep, re.IGNORECASE)
                if m_rep:
                    names = [normalize_person_name(n, text) for n in re.findall(NAME2_RE, m_rep.group(1)) if not any(w.lower() in DISCARD_TITLES for w in n.split())]
                    podpisujacy = [{"imie_nazwisko": n} for n in names if n]

            mocodawcy = [{
                "data": {
                    "imie_nazwisko": None,
                    "krs": krs,
                    "nazwa": _norm_space(nazwa) if nazwa else None,
                    "nip": nip,
                    "typ": "osoba_prawna",
                },
                "podpisujacy": podpisujacy,
            }]

    # Pełnomocnicy
    all_signers = {p["imie_nazwisko"] for m in mocodawcy for p in m.get("podpisujacy", []) if p.get("imie_nazwisko")}
    peln = []
    flat_text = _flat(text)

    # 1. Attorneys with legal titles in flat text
    for mm in re.finditer(r"(?:r\.\s*pr\.|adw\.|mec\.|radc[ay]\s+prawn[ya]|adwokat)\s+(" + NAME2_RE + r")", flat_text):
        name = normalize_person_name(mm.group(1).strip(), text)
        if name and name not in [p["imie_nazwisko"] for p in peln] and name not in all_signers:
            m_p = re.search(re.escape(name) + r"[^\n\(\)]*?(?:\([^)]*?)?PESEL[:\s]*(\d{11})", flat_text, re.IGNORECASE)
            pesel = m_p.group(1) if m_p else None
            peln.append({"imie_nazwisko": name, "pesel": pesel})

    # 2. If no legal titles found or for additional employees in sec
    m_sec = re.search(r"udziela(?:m|ją)?\s+pełnomocnictwa[:\s]*(.*?)(?=do\s+reprezentowania|\Z)", text, re.DOTALL | re.IGNORECASE)
    sec = m_sec.group(1) if m_sec else ""
    sec_flat = _flat(sec)

    for m in re.finditer(r"([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+ [A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+(?:-[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)?)\s*\(([^)]+)\)", sec_flat):
        name = normalize_person_name(m.group(1).strip(), text)
        details = m.group(2)
        m_pesel = re.search(r"PESEL[:\s]*(\d{11})", details)
        pesel = m_pesel.group(1) if m_pesel else None
        if name and name not in [p["imie_nazwisko"] for p in peln] and name not in all_signers:
            peln.append({"imie_nazwisko": name, "pesel": pesel})

    if not peln:
        for l in sec.splitlines():
            l = l.strip()
            if not l:
                continue
            l_clean = re.sub(r"^\d+[\.\)]\s*", "", l)
            m_name = re.search(r"^([A-ZĄĆĘŁŃÓŚŹŻa-ząćęłńóśźż]+ [A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+(?:-[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)?)", l_clean)
            if m_name:
                cand = m_name.group(1).strip()
                parts = cand.split()
                if len(parts) == 2 and parts[0][0].isalpha() and parts[1][0].isupper():
                    cand = parts[0].capitalize() + " " + parts[1]
                    cand = normalize_person_name(cand, text)
                    if cand and cand not in [p["imie_nazwisko"] for p in peln] and cand not in all_signers and cand.lower() not in ("prezes zarządu", "wiceprezes zarządu"):
                        m_pesel = re.search(r"PESEL[:\s]*(\d{11})", l)
                        pesel = m_pesel.group(1) if m_pesel else None
                        peln.append({"imie_nazwisko": cand, "pesel": pesel})

    return {"mocodawca": mocodawcy, "pelnomocnicy": peln}


# ---------------------------------------------------------------- przelew
IBAN_RE = r"(?:PL\s*)?(\d{2}(?:[\s-]*\d{4}){6}|\d{26})"
# Kwota z groszami: "7 500,00", "7500.00", "7.500,00", "1,234.56" (separator tysięcy . lub ,).
# (?!\d)(?![.,]\d) — grosze muszą się kończyć: "7.500,00" to nie 7,50, a "12.03.2026" to nie kwota.
AMT_RE = r"(\d[\d\s]*(?:[.,]\d{3})*[,.]\d{2})(?!\d)(?![.,]\d)"


def _parse_amount(s):
    """'7 500,00' / '7.500,00' / '1,234.56' -> float > 0, inaczej None."""
    s = re.sub(r"\s+", "", s or "")
    if not s:
        return None
    dec = max(s.rfind(","), s.rfind("."))
    if dec >= 0:
        s = re.sub(r"[.,]", "", s[:dec]) + "." + s[dec + 1:]
    try:
        val = abs(float(s))
    except ValueError:
        return None
    return val if val > 0 else None


_ACC_SENDER = ("z rachunku", "rachunek nadawcy", "rachunek zleceniodawcy", "rachunek obciążany",
               "rachunek obciążony", "rachunek płatnika", "konto nadawcy", "z konta", "nadawca", "zleceniodawca")
_ACC_RECIPIENT = ("na rachunek", "rachunek odbiorcy", "rachunek beneficjenta", "rachunek uznawany",
                  "rachunek kontrahenta", "rachunek docelowy", "konto odbiorcy", "na konto", "odbiorca", "beneficjent")


def _acc_role(text, start, floor=0):
    """Rola rachunku wg najbliższej etykiety przed nim (do 80 znaków): 'nadawca' / 'odbiorca' / None."""
    window = " ".join(text[max(floor, start - 80):start].lower().split())
    best, role = -1, None
    for labels, r in ((_ACC_SENDER, "nadawca"), (_ACC_RECIPIENT, "odbiorca")):
        for lab in labels:
            i = window.rfind(lab)
            if i > best:
                best, role = i, r
    return role


def extract_przelew(text, config, llm_fields=None):
    # 1. Rachunki (z_rachunku = nadawca, na_rachunek = odbiorca)
    acc_m = [m for m in re.finditer(IBAN_RE, text, re.IGNORECASE) if len(re.sub(r"\D", "", m.group(1))) == 26]
    clean_accs = [re.sub(r"\D", "", m.group(1)) for m in acc_m]
    z_r = clean_accs[0] if len(clean_accs) > 0 else None
    na_r = clean_accs[1] if len(clean_accs) > 1 else None
    # Domyślnie pierwszy rachunek = nadawca. Zamiana TYLKO, gdy etykiety wprost mówią odwrotnie.
    if len(acc_m) >= 2 and _acc_role(text, acc_m[0].start()) == "odbiorca" \
            and _acc_role(text, acc_m[1].start(), floor=acc_m[0].end()) == "nadawca":
        z_r, na_r = na_r, z_r

    # 2. Data operacji
    data = None
    # 2a. PLN DD.MM.YYYY
    m_pln_d = re.search(r"PLN\s+(\d{1,2}[-./]\d{1,2}[-./]\d{4})", text)
    if m_pln_d:
        data = _parse_pl_date(m_pln_d.group(1))
    # 2b. Data operacji / transakcji / wykonania / w dniu
    if not data:
        m_date = re.search(
            r"(?:Data(?:\s+(?:operacji|transakcji|wykonania))?|w\s+dniu)\s*[:.\s]+(\d{4}-\d{2}-\d{2}|\d{1,2}[-./\s](?:[A-Za-ząćęłńóśźż]+|\d{1,2})[-./\s]\d{4})",
            text,
            re.IGNORECASE,
        )
        if m_date:
            data = _parse_pl_date(m_date.group(1))
    # 2c. YYYY-MM-DD Z rachunku
    if not data:
        m_z = re.search(r"(\d{4}-\d{2}-\d{2})\s+Z\s+rachunku", text, re.IGNORECASE)
        if m_z:
            data = _parse_pl_date(m_z.group(1))
    # 2d. data wykonania DD MM YYYY
    if not data:
        m_wyk = re.search(r"data\s+wykonania\s*[:\s]*(\d{1,2}\s+\d{2}\s+\d{4})", text, re.IGNORECASE)
        if m_wyk:
            parts = m_wyk.group(1).split()
            data = f"{parts[2]}-{int(parts[1]):02d}-{int(parts[0]):02d}"
    # 2e. Standalone date line
    if not data:
        for line in text.splitlines():
            line_str = line.strip()
            if re.match(r"^\d{4}-\d{2}-\d{2}$", line_str):
                data = line_str
                break
    # 2f. Fallback line scan
    if not data:
        for line in text.splitlines():
            if re.search(r"wydruk|wygenerowa", line, re.IGNORECASE):
                continue
            d = _parse_pl_date(line)
            if d:
                data = d
                break

    # 3. Kwota operacji
    kwota = None
    m_kw = re.search(
        r"(?:Kwota\s+(?:operacji|przelewu|transakcji)|Kwota(?:\s+przelewu)?|Wartość(?:\s+operacji)?|w\s+kwocie)\s*[:.\s]*([A-Z]{3}\s*)?[-–—]?" + AMT_RE,
        text,
        re.IGNORECASE,
    )
    if m_kw:
        kwota = _parse_amount(m_kw.group(2))
    if kwota is None:
        for line in text.splitlines():
            if any(x in line.lower() for x in ("opłat", "prowizj", "dniu", "data")):
                continue
            m_l = re.search(r"[-–—]?" + AMT_RE + r"\s*(?:PLN|zł)\b", line)
            if m_l:
                kwota = _parse_amount(m_l.group(1))
                if kwota is not None:
                    break
    if kwota is None:
        m_tbl = re.search(r"Kwota[^\n]*\n\s*[-–—]?" + AMT_RE + r"\s*(?:PLN|zł)\b", text, re.IGNORECASE)
        if m_tbl:
            kwota = _parse_amount(m_tbl.group(1))
    if kwota is None:
        for m_all in re.finditer(r"[-–—]?" + AMT_RE + r"\s*(?:PLN|zł)\b", text):
            kwota = _parse_amount(m_all.group(1))
            if kwota is not None:
                break

    # 4. Numer transakcji / referencyjny
    nr = None
    m_cen = re.search(r"\b(CEN\d{10,})\b", text)
    if m_cen:
        nr = m_cen.group(1)
    if not nr:
        m_col = re.search(r"Numer\s+([A-Za-z0-9]{6,})\s*\n\s*transakcj", text, re.IGNORECASE)
        if m_col:
            nr = m_col.group(1)
    if not nr:
        m_nr = re.search(
            r"(?:Numer\s+(?:referencyjny|transakcji)(?:\s+operacji)?|Nr\s+ref\.?(?:\s+operacji)?)\s*[:.\s]+([A-Za-z0-9]{6,})",
            text,
            re.IGNORECASE,
        )
        if m_nr and m_nr.group(1).lower() not in ("transakcji", "operacji", "referencyjny", "zlecenia"):
            nr = m_nr.group(1).strip()

    # 5. Tytuł przelewu
    tytul = None
    m_tyt = re.search(
        r"(?:^|\n)\s*(?:Tytuł\s+przelewu|Tytułem|Tytuł)\s*[:.\s|–—]*\n?\s*([^\n].*?)(?=(?:\n\s*(?:Przelew\s+z\s+rachunku|Numer|Nr\s+ref|Identyfikator|Kwota|Wartość|Data|Status|Typ|Waluta|Kanał|Opłata|Nadawca|Odbiorca|Beneficjent|Rachunek|Bank\s+nie\s+ponosi|Dokument|Z\s+poważaniem|ZREALIZOWANO))|\Z)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if not m_tyt:
        m_tyt = re.search(
            r"(?:^|\n)\s*(?:Opis|Szczegóły\s+p[łli][aą]tności)\s*[:.\s|–—]*\n?\s*([^\n].*?)(?=(?:\n\s*(?:Przelew\s+z\s+rachunku|Numer|Nr\s+ref|Identyfikator|Kwota|Wartość|Data|Status|Typ|Waluta|Kanał|Opłata\s+(?:za|od\s+transakcji)|Nadawca|Odbiorca|Beneficjent|Rachunek|Bank\s+nie\s+ponosi|Dokument|Z\s+poważaniem|ZREALIZOWANO))|\Z)",
            text,
            re.DOTALL | re.IGNORECASE,
        )
    if not m_tyt:
        m_tyt = re.search(
            r"((?:wpis\s+od\s+odw|opłata\s+skarbowa\s+od\s+pełnomocn)[^\n]+(?:\n[^\n]+)*?)(?=(?:\n\s*(?:Źródło|Kwota|Numer|Nr\s+ref|Identyfikator|Data|Status|Typ|Dane|Rachunek|Bank|Dokument))|\Z)",
            text,
            re.IGNORECASE,
        )
    if m_tyt:
        raw_t = m_tyt.group(1)
        raw_t = re.split(r"\s+(?:Identyfikator\s+operacji|IDENTYFIKATOR\s+OPERACJI|ZREALIZOWANO)\b", raw_t)[0]
        raw_t = re.sub(r"\s+(?:Bank\s+nie\s+ponosi|Dokument\s+został|Niniejszy\s+dokument|Opłata\s+pobrana).*$", "", raw_t, flags=re.IGNORECASE)
        raw_t = re.sub(r"\s+Strona\s+\d+.*$", "", raw_t, flags=re.IGNORECASE)
        t_clean = " ".join(raw_t.split()).strip(" :|„”\"'»«.-")
        if t_clean:
            tytul = t_clean

    return {
        "przelew_bankowy": {
            "data_operacji": data,
            "kwota": kwota,
            "na_rachunek": na_r,
            "numer_transakcji": nr,
            "tytul": tytul,
            "z_rachunku": z_r,
        }
    }


EXTRACTORS = {
    "odwolanie": extract_odwolanie,
    "pelnomocnictwo": extract_pelnomocnictwo,
    "przelew": extract_przelew,
}


# ---------------------------------------------------------------- nowe klasy: silnik reguł z YAML
# Klasa spoza EXTRACTORS jest ekstrahowana WYŁĄCZNIE według sekcji `extract` w
# config/document_types.yaml — dodanie nowego typu dokumentu nie wymaga zmian w kodzie.
# Struktura JSON-a = zagnieżdżenie kluczy w `extract`; liść = {rule: <budulec>, ...}.
GENERIC_RULE_METHOD = {
    "header_date": "regex", "party_id": "regex", "numer_postepowania": "regex", "regex": "regex",
    "header_place": "heurystyka", "party_name": "heurystyka", "choice": "heurystyka",
    "signers": "heurystyka", "const": "heurystyka", "llm_only": "llm",
}
_ID_LEN = {"nip": (10,), "krs": (10,), "regon": (9, 14), "pesel": (11,)}


def _fields_to_spec(fields):
    """Klasa z samą listą `fields` (bez reguł) -> wszystkie pola tylko z LLM-a (z walidacją)."""
    spec = {}
    for f in fields:
        parts = f.split(".")
        cur = spec
        for p in parts[:-1]:
            cur = cur.setdefault(p.replace("[]", ""), {})
            if p.endswith("[]"):
                cur["_as_list"] = True
        last = parts[-1]
        cur[last.replace("[]", "")] = {"rule": "llm_only", "llm": True, "list": last.endswith("[]")}
    return spec


def _class_spec(config, doc_type):
    cls = config.get("classes", {}).get(doc_type, {})
    return cls.get("extract") or _fields_to_spec(cls.get("fields", []))


def generic_rules(config, doc_type):
    """{ścieżka: reguła} dla liści sekcji `extract` (ścieżki z kropkami, bez indeksów list)."""
    out = {}

    def walk(node, prefix):
        for key, val in node.items():
            if key.startswith("_") or not isinstance(val, dict):
                continue
            path = f"{prefix}.{key}" if prefix else key
            if "rule" in val:
                out[path] = val
            else:
                walk(val, path)

    walk(_class_spec(config, doc_type), "")
    return out


def _as_labels(v):
    return [v] if isinstance(v, str) else list(v or [])


def _normalize_value(val, how):
    if val is None:
        return None
    if how == "digits":
        val = _digits(val)
    elif how == "nospace":
        val = re.sub(r"\s+", "", val)
    elif how == "date":
        return _parse_pl_date(val)
    elif how == "amount":
        return _parse_amount(val)
    else:
        val = _norm_space(val)
        if how == "upper":
            val = val.upper()
        elif how == "lower":
            val = val.lower()
    return val or None


def _apply_rule(rule, text):
    """Jedna reguła z YAML -> wartość albo None (brak danej = null, bez zgadywania)."""
    kind = rule.get("rule")
    if kind == "header_date":
        return _header_date_place(text)[0]
    if kind == "header_place":
        return _header_date_place(text)[1]
    if kind in ("party_name", "party_id"):
        block = ""
        for label in _as_labels(rule.get("label")):
            block = _party_block(text, label, next_labels=tuple(_as_labels(rule.get("until"))))
            if block.strip():
                break
        if not block.strip():
            return None  # brak etykiety strony -> null (nie bierzemy NIP-u z innej części pisma)
        if kind == "party_id":
            if rule.get("id") == "pesel":
                return _find(r"PESEL[:\s]*(\d{11})", block)
            krs, nip, regon = _ids_from_block(block)
            return {"krs": krs, "nip": nip, "regon": regon}.get(rule.get("id"))
        if "konsorcjum" in block.lower():
            m = re.search(r"1\)\s*(?:Lider\s+Konsorcjum\s*:?\s*)?(?:\n\s*)?([^\n,\(]+)", block, re.IGNORECASE)
            if m and m.group(1).strip():
                return m.group(1).strip()
        return _first_name_line(block)
    if kind == "numer_postepowania":
        labels = _as_labels(rule.get("labels"))
        return _numer_postepowania(text, "(?:" + "|".join(labels) + ")" if labels else NR_POST_LABELS)
    if kind == "regex":
        src = _flat(text) if rule.get("flat") else text
        flags = re.MULTILINE | (re.IGNORECASE if rule.get("ignore_case", True) else 0)
        m = re.search(rule["pattern"], src, flags)
        return _normalize_value(m.group(rule.get("group", 1)), rule.get("normalize")) if m else None
    if kind == "choice":
        best = None  # wygrywa opcja, która występuje w tekście najwcześniej
        for value, pattern in rule.get("options", []):
            m = re.search(pattern, text, re.IGNORECASE)
            if m and (best is None or m.start() < best[0]):
                best = (m.start(), value)
        return best[1] if best else None
    if kind == "signers":
        return parse_signers_block(text)
    if kind == "const":
        return rule.get("value")
    if kind == "llm_only":
        return [] if rule.get("list") else None
    return None


def _grounded(value, text):
    """Czy wartość (np. z LLM-a) występuje w tekście dokumentu — ochrona przed halucynacją."""
    if value is None:
        return False
    v = str(value)
    if re.fullmatch(r"[\d\s\-]+", v):
        return bool(_digits(v)) and _digits(v) in _digits(text)
    words = [w for w in re.findall(r"\w+", v.lower()) if len(w) > 2]
    low = (text or "").lower()
    if not words:
        return v.lower() in low
    return sum(1 for w in words if w in low) >= 0.8 * len(words)


def _validate_llm(rule, value, text, path=""):
    """Wartość z LLM-a przyjmujemy tylko po walidacji formatu i gdy występuje w tekście."""
    if value is None:
        return None
    kind = rule.get("rule")
    leaf = path.rsplit(".", 1)[-1]
    if kind == "signers" or rule.get("list"):
        if not isinstance(value, list):
            return None
        out = []
        for item in value:
            name = item.get("imie_nazwisko") if isinstance(item, dict) else item
            if isinstance(name, str):
                name = " ".join(name.split())
                if re.fullmatch(NAME2_RE, name) and not is_inflected_name(name) \
                        and _grounded(name.split()[-1][:4], text):
                    out.append({"imie_nazwisko": name})
        return out or None
    if isinstance(value, bool) or isinstance(value, (dict, list)):
        return None
    if isinstance(value, (int, float)):
        return value if _grounded(str(int(value)), text) else None
    s = " ".join(str(value).split())
    idk = rule.get("id") or (leaf if leaf in _ID_LEN else None)
    if kind == "party_id" or idk:
        d = _digits(s)
        return d if d and len(d) in _ID_LEN.get(idk, (len(d),)) and d in _digits(text) else None
    if kind == "header_date" or (kind == "llm_only" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", s)):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return None
        y, mo, da = s.split("-")
        ok = s in text or re.search(rf"(?<!\d)0?{int(da)}[.\-/ ]0?{int(mo)}[.\-/ ]{y}", text) \
            or re.search(rf"(?<!\d)0?{int(da)}\s+\w+\s+{y}", text)
        return s if ok else None
    if kind == "choice":
        return s if s in [o[0] for o in rule.get("options", [])] else None
    if kind == "numer_postepowania":
        s = re.sub(r"\s+", "", s)
        ok = re.fullmatch(NR_POST_TOKEN, s) and s.lower() in re.sub(r"\s+", "", (text or "").lower())
        return s if ok else None
    if rule.get("validate") and not re.fullmatch(rule["validate"], s):
        return None
    return s if s and _grounded(s, text) else None


def _llm_lookup(raw, path):
    """Wartość pola z odpowiedzi LLM-a: klucz płaski 'a.b', zagnieżdżony {'a': {'b'}} lub lista [{...}]."""
    if not isinstance(raw, dict):
        return None
    if path in raw:
        return raw[path]
    cur = raw
    for part in path.split("."):
        if isinstance(cur, list):
            cur = cur[0] if cur and isinstance(cur[0], dict) else None
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def extract_generic(text, config, doc_type, llm_fields=None):
    """Ekstrakcja klasy spoza EXTRACTORS wg sekcji `extract` z YAML (bez kodu per typ)."""
    llm_fields = llm_fields or {}

    def build(node, prefix):
        out = {}
        for key, val in node.items():
            if key.startswith("_") or not isinstance(val, dict):
                continue
            path = f"{prefix}.{key}" if prefix else key
            if "rule" in val:
                v = _apply_rule(val, text)
                if v in (None, []) and val.get("llm"):
                    lv = _validate_llm(val, _llm_lookup(llm_fields, path), text, path)
                    if lv is not None:
                        v = lv
                out[key] = v
            else:
                sub = build(val, path)
                out[key] = [sub] if val.get("_as_list") else sub
        return out

    return build(_class_spec(config, doc_type), "")


def generic_missing(config, doc_type, draft):
    """Ścieżki pól z `llm: true`, których reguły nie wypełniły — tylko o nie pytamy LLM-a."""
    return [p for p, r in generic_rules(config, doc_type).items()
            if r.get("llm") and _llm_lookup(draft, p) in (None, [])]


def _rule_hint(rule, path):
    if rule.get("hint"):
        return rule["hint"]
    kind = rule.get("rule")
    leaf = path.rsplit(".", 1)[-1]
    idk = rule.get("id") or leaf
    if idk in _ID_LEN:
        return f"{idk.upper()} — same cyfry ({' lub '.join(map(str, _ID_LEN[idk]))})"
    if kind == "header_date" or leaf.startswith("data"):
        return "data w formacie YYYY-MM-DD"
    if kind == "choice":
        return "dokładnie jedna z wartości: " + " | ".join(o[0] for o in rule.get("options", []))
    if kind == "signers" or rule.get("list"):
        return 'lista obiektów {"imie_nazwisko": "Imię Nazwisko"} w mianowniku'
    if kind == "numer_postepowania":
        return "numer referencyjny / znak postępowania, bez spacji"
    if kind == "party_name":
        return "pełna nazwa podmiotu (przy konsorcjum — lider)"
    return "wartość pola przepisana z tekstu"


def generic_llm_prompt(config, doc_type, missing):
    cls = config.get("classes", {}).get(doc_type, {})
    rules = generic_rules(config, doc_type)
    lines = "\n".join(f'- "{p}": {_rule_hint(rules[p], p)}' for p in missing)
    return (
        (cls.get("prompt_hint") or f"Dokument typu: {cls.get('label', doc_type)}.")
        + "\nZwróć WYŁĄCZNIE płaski JSON z dokładnie tymi kluczami:\n" + lines
        + "\nWartości przepisuj z tekstu; gdy danej nie ma w tekście, wpisz null — nie zgaduj."
    )


_LLM_NULLS = {"", "null", "none", "brak", "brak danych", "n/a", "nie dotyczy", "-", "—"}


def _clean_llm_value(v):
    """Odpowiedź LLM-a: puste / 'null' / 'brak' -> None (nigdy pusty string w JSON-ie)."""
    if isinstance(v, str):
        v = " ".join(v.split())
        return None if v.lower() in _LLM_NULLS else v
    if isinstance(v, dict):
        return {k: _clean_llm_value(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean_llm_value(x) for x in v]
    return v


def deterministic_result(text, config, doc_type):
    """Wynik bez żadnego wywołania LLM-a — punkt odniesienia dla metody 'LLM' w GUI."""
    from llm import llm_disabled

    with llm_disabled():
        if doc_type in EXTRACTORS:
            return EXTRACTORS[doc_type](text, config, {})
        return extract_generic(text, config, doc_type, {})


def extract_document(pdf_path, config, use_llm=True, trace=None):
    """Pełny pipeline dla jednego PDF-a. Zwraca (wynik_json, typ, info).

    trace (opcjonalny dict) dostaje metadane dla GUI: teksty stron, tekst, pola od LLM-a.
    Metadane nigdy nie trafiają do wynikowego JSON-a.
    """
    from llm import extract_fields_llm

    pages, info = extract_pages(pdf_path)
    text = "\n".join(pages)
    doc_type = classify(text, config, filename=os.path.basename(pdf_path))
    if trace is not None:
        trace.update({"pages": pages, "text": text, "doc_type": doc_type, "llm_fields": {}, "llm_requested": []})
    if doc_type == "nieznany":
        return {}, "nieznany", {**info, "error": "Nie udało się znaleźć typu dokumentu"}

    if doc_type in EXTRACTORS:
        extractor = EXTRACTORS[doc_type]
    else:
        def extractor(txt, cfg, llm):
            return extract_generic(txt, cfg, doc_type, llm)

    result = extractor(text, config, {})
    llm_fields = {}
    if use_llm:
        # LLM tylko dla pól, których regex/heurystyka nie dały — po 1. przebiegu.
        if doc_type == "odwolanie":
            needs = []
            if result.get("OdwolanieKIO", {}).get("czynnosc_powod_id") is None:
                needs.append("czynnosc_powod_id")
            if not result.get("ZamowieniePubliczne", {}).get("tytul"):
                needs.append("tytul")
            if needs:
                if trace is not None:
                    trace["llm_requested"] = list(needs)
                prompt = (
                    "Wyciągnij z tekstu TYLKO te pola i zwróć je jako JSON: "
                    + ", ".join(needs)
                    + ". czynnosc_powod_id to liczba 1-12 wg listy: "
                    + "; ".join(f"{k}: {v}" for k, v in config["czynnosc_powod"].items())
                    + ". Ustal ją WYŁĄCZNIE na podstawie pierwszego zarzutu. "
                    "Daty w formacie YYYY-MM-DD. Brak danej = null."
                )
                llm_fields = _clean_llm_value(extract_fields_llm(text, prompt) or {})
        elif doc_type not in EXTRACTORS:
            missing = generic_missing(config, doc_type, result)
            if missing:
                if trace is not None:
                    trace["llm_requested"] = list(missing)
                prompt = generic_llm_prompt(config, doc_type, missing)
                llm_fields = _clean_llm_value(extract_fields_llm(text, prompt) or {})
        if llm_fields:
            result = extractor(text, config, llm_fields)
    if trace is not None:
        trace["llm_fields"] = llm_fields
    return result, doc_type, info
