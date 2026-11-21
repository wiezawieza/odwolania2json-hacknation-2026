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


def load_config(path="config/document_types.yaml"):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------- tekst z PDF
def _text_via_fitz(pdf_path):
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf
    doc = pymupdf.open(pdf_path)
    pages = [page.get_text() for page in doc]
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
    used = False
    new_pages = list(pages)
    cache_dir = os.path.join(os.path.dirname(__file__), ".cache_ocr")
    os.makedirs(cache_dir, exist_ok=True)
    mtime = os.path.getmtime(pdf_path) if os.path.exists(pdf_path) else 0

    for i, page in enumerate(doc):
        if len((pages[i] if i < len(pages) else "").strip()) >= 200:
            continue
        import hashlib
        cache_key = hashlib.sha256(f"{os.path.abspath(pdf_path)}_{mtime}_{i}".encode("utf-8")).hexdigest()[:16]
        cache_file = os.path.join(cache_dir, f"{cache_key}.txt")
        if os.path.isfile(cache_file):
            try:
                with open(cache_file, encoding="utf-8") as cf:
                    new_pages[i] = cf.read()
                used = True
                continue
            except Exception:
                pass
        try:
            pix = page.get_pixmap(dpi=300)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            txt = pytesseract.image_to_string(img, lang="pol")
            try:
                with open(cache_file, "w", encoding="utf-8") as cf:
                    cf.write(txt)
            except Exception:
                pass
            new_pages[i] = txt
            used = True
        except Exception:
            continue
    return new_pages, used


def extract_text(pdf_path):
    """Zwraca (tekst_łączny, info dict). Nigdy nie rzuca wyjątku."""
    info = {"ocr": False, "pages": 0, "chars": 0}
    try:
        pages = _text_via_fitz(pdf_path)
    except Exception:
        try:
            pages = _text_via_pdftotext(pdf_path)
        except Exception:
            return "", {**info, "error": "nie można odczytać PDF"}
    info["pages"] = len(pages)
    info["chars"] = sum(len(p) for p in pages)
    avg = info["chars"] / max(1, info["pages"])
    if avg < 200:
        pages, used = _ocr_pages(pdf_path, pages)
        info["ocr"] = used
        if not used:
            info["needs_ocr"] = True
        info["chars"] = sum(len(p) for p in pages)
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
    pat_line = re.compile(
        r"^\s*([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+(?:\s+[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)?)\s*,\s*(?:dnia\s+)?(.+)",
        re.MULTILINE,
    )
    for m in pat_line.finditer(p1):
        place = m.group(1).strip()
        date = _parse_pl_date(m.group(2))
        if date and place:
            return date, place
    # Fallback tylko dla linii 1-3 na skanach: 'Łódź 05.06.2026r.'
    for l in p1.splitlines()[:3]:
        m_top = re.match(r"^\s*([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)\s+(\d{1,2}[./]\d{1,2}[./]\d{4}\s*r?\.?)", l.strip())
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
            if "@" not in raw_m and peln_name:
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
        tytul = _norm_space(tytul) if tytul else llm_fields.get("tytul")

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

    flat_all = _flat(text)
    m_nr = re.search(r"(?:n[rt]\.?\s+referencyjny(?:\s+sprawy)?|numer\s+postępowania|znak\s+sprawy)[:\s\.]+([A-Za-z0-9][A-Za-z0-9\./\-\s]{2,30})", flat_all, re.IGNORECASE)
    if m_nr:
        nr_raw = m_nr.group(1).rstrip(".,; ")
        nr_post = re.sub(r"\s*/\s*", "/", nr_raw)
        nr_post = re.sub(r"\s+", "", nr_post)
    else:
        nr_post = None

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

        nazwa1 = _norm_space(re.split(r",\s*ul\.|\s+z\s+siedzibą", part1)[0])
        nazwa2 = _norm_space(re.split(r",\s*ul\.|\s+z\s+siedzibą", part2)[0])

        krs1, nip1, regon1 = _ids_from_block(part1)
        krs2, nip2, regon2 = _ids_from_block(part2)

        m_sig1 = re.search(r"W\s+imieniu\s+" + re.escape(nazwa1[:10]) + r".*?(?=W\s+imieniu|Pełnomocnictwo\s+przyjmuj|\Z)", text, re.DOTALL | re.IGNORECASE)
        m_sig2 = re.search(r"W\s+imieniu\s+" + re.escape(nazwa2[:10]) + r".*?(?=W\s+imieniu|Pełnomocnictwo\s+przyjmuj|\Z)", text, re.DOTALL | re.IGNORECASE)

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


def extract_przelew(text, config, llm_fields=None):
    # 1. Rachunki (z_rachunku = nadawca, na_rachunek = odbiorca)
    raw_accs = re.findall(IBAN_RE, text, re.IGNORECASE)
    clean_accs = [re.sub(r"\D", "", a) for a in raw_accs if len(re.sub(r"\D", "", a)) == 26]
    z_r = clean_accs[0] if len(clean_accs) > 0 else None
    na_r = clean_accs[1] if len(clean_accs) > 1 else None

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
        r"(?:Kwota\s+(?:operacji|przelewu|transakcji)|Kwota(?:\s+przelewu)?|Wartość(?:\s+operacji)?|w\s+kwocie)\s*[:.\s]*([A-Z]{3}\s*)?[-–—]?(\d[\d\s]*[,\.]\d{2})",
        text,
        re.IGNORECASE,
    )
    if m_kw:
        val_str = m_kw.group(2).replace(" ", "").replace(",", ".")
        try:
            val = abs(float(val_str))
            if val > 0:
                kwota = val
        except ValueError:
            pass
    if kwota is None:
        for line in text.splitlines():
            if any(x in line.lower() for x in ("opłat", "prowizj", "dniu", "data")):
                continue
            m_l = re.search(r"[-–—]?(\d[\d\s]*[,\.]\d{2})\s*(?:PLN|zł)\b", line)
            if m_l:
                try:
                    val = abs(float(m_l.group(1).replace(" ", "").replace(",", ".")))
                    if val > 0:
                        kwota = val
                        break
                except ValueError:
                    pass
    if kwota is None:
        m_tbl = re.search(r"Kwota[^\n]*\n\s*[-–—]?(\d[\d\s]*[,\.]\d{2})\s*(?:PLN|zł)\b", text, re.IGNORECASE)
        if m_tbl:
            try:
                kwota = abs(float(m_tbl.group(1).replace(" ", "").replace(",", ".")))
            except ValueError:
                pass
    if kwota is None:
        for m_all in re.finditer(r"[-–—]?(\d[\d\s]*[,\.]\d{2})\s*(?:PLN|zł)\b", text):
            try:
                val = abs(float(m_all.group(1).replace(" ", "").replace(",", ".")))
                if val > 0:
                    kwota = val
                    break
            except ValueError:
                pass

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


def extract_document(pdf_path, config, use_llm=True):
    """Pełny pipeline dla jednego PDF-a. Zwraca (wynik_json, typ, info)."""
    from llm import extract_fields_llm

    text, info = extract_text(pdf_path)
    doc_type = classify(text, config, filename=os.path.basename(pdf_path))
    if doc_type == "nieznany":
        return {}, "nieznany", {**info, "error": "Nie udało się znaleźć typu dokumentu"}

    llm_fields = {}
    if use_llm:
        # LLM tylko gdy regex/heurystyka czegoś nie dała — sprawdzamy po 1. przebiegu.
        draft = EXTRACTORS[doc_type](text, config, {})
        needs = []
        if doc_type == "odwolanie":
            if draft["OdwolanieKIO"]["czynnosc_powod_id"] is None:
                needs.append("czynnosc_powod_id")
            if not draft["ZamowieniePubliczne"]["tytul"]:
                needs.append("tytul")
        if needs:
            prompt = (
                "Wyciągnij z tekstu TYLKO te pola i zwróć je jako JSON: "
                + ", ".join(needs)
                + ". czynnosc_powod_id to liczba 1-12 wg listy: "
                + "; ".join(f"{k}: {v}" for k, v in config["czynnosc_powod"].items())
                + ". Ustal ją WYŁĄCZNIE na podstawie pierwszego zarzutu. "
                "Daty w formacie YYYY-MM-DD. Brak danej = null."
            )
            llm_fields = extract_fields_llm(text, prompt) or {}
    result = EXTRACTORS[doc_type](text, config, llm_fields)
    return result, doc_type, info
