"""Spójność sprawy (hasło UZP: 'Zrozum sprawę. Nie tylko dokument.').

Po ekstrakcji 3 dokumentów jednej sprawy porównujemy pola, które muszą
się zgadzać między dokumentami, i uzupełniamy braki z innych dokumentów
tej samej sprawy. Nie wymyślamy danych — uzupełniamy tylko tym, co jest
w innym dokumencie tej samej sprawy. Niezgodności trafiają do logu.
"""

import json
import re

# Stawki wpisu od odwołania (rozporządzenie Prezesa Rady Ministrów w sprawie wpisu od
# odwołania, § 2 ust. 1): dostawy i usługi / roboty budowlane.
WPIS_PONIZEJ_PROGOW = (7500.0, 10000.0)
WPIS_OD_PROGOW = (15000.0, 20000.0)


def _norm(s):
    return " ".join(str(s).split()).lower() if s else None


def check_case(results):
    """results: dict {doc_type: wynik_json}. Zwraca (results_poprawione, uwagi[])."""
    uwagi = []
    odw = results.get("odwolanie") or {}
    peln = results.get("pelnomocnictwo") or {}
    prz = results.get("przelew") or {}

    odw_party = (odw.get("Odwolujacy") or [{}])[0]
    moc = (peln.get("mocodawca") or [{}])[0].get("data") or {}

    # 1. NIP / nazwa odwołującego vs mocodawca
    if odw_party.get("nip") and moc.get("nip") and odw_party["nip"] != moc["nip"]:
        uwagi.append(f"NIP odwołującego ({odw_party['nip']}) != NIP mocodawcy ({moc['nip']})")
    if not moc.get("nip") and odw_party.get("nip"):
        moc["nip"] = odw_party["nip"]
        uwagi.append("Uzupełniono NIP mocodawcy z odwołania")
    if not moc.get("nazwa") and odw_party.get("nazwa"):
        moc["nazwa"] = odw_party["nazwa"]
        uwagi.append("Uzupełniono nazwę mocodawcy z odwołania")
    if not odw_party.get("nip") and moc.get("nip"):
        odw_party["nip"] = moc["nip"]
        uwagi.append("Uzupełniono NIP odwołującego z pełnomocnictwa")

    # 2. Numer postępowania: odwołanie vs tytuł przelewu
    nr = (odw.get("ZamowieniePubliczne") or {}).get("numer_postepowania")
    tytul_prz = (prz.get("przelew_bankowy") or {}).get("tytul") or ""
    if nr and tytul_prz and nr not in tytul_prz:
        uwagi.append(f"Numer postępowania {nr} nie występuje w tytule przelewu — sprawdź spójność sprawy")

    # 3. Pełnomocnik z odwołania vs pełnomocnicy z pełnomocnictwa
    peln_odw = (odw_party.get("pelnomocnik") or {}).get("imie_nazwisko")
    peln_list = [p.get("imie_nazwisko") for p in peln.get("pelnomocnicy") or []]
    if peln_odw and peln_list and peln_odw not in peln_list:
        uwagi.append(f"Pełnomocnik z odwołania ({peln_odw}) nie ma go na liście pełnomocników")

    return results, uwagi


# ---------------------------------------------------------------- widok sprawy (GUI / CLI)
# docs: lista {"type": typ, "result": json, "text": pełny tekst, "filename": nazwa}
def _first(docs, typ):
    return next((d for d in docs if d.get("type") == typ), None)


def _all(docs, typ):
    return [d for d in docs if d.get("type") == typ]


def fmt_pln(v):
    return f"{v:,.2f}".replace(",", " ").replace(".", ",") + " zł"


def wpis_z_tekstu(text):
    """Kwota wpisu wskazana w odwołaniu ('Wpis od odwołania: 15 000,00 zł') albo None."""
    m = re.search(
        r"wpis\w*\s+od\s+odwołania\W{0,5}(?:w\s+(?:wysokości|kwocie)\s+)?"
        r"(\d{1,3}(?:[  .]\d{3})+|\d+)(?:[,.](\d{2}))?",
        text or "", re.IGNORECASE,
    )
    if not m:
        return None
    v = float(re.sub(r"\D", "", m.group(1)) + "." + (m.group(2) or "00"))
    return v if v > 0 else None


def prog_unijny(text, odw_result):
    """True: wartość zamówienia >= progi UE, False: poniżej, None: nie wiadomo."""
    low = " ".join((text or "").lower().split())
    if re.search(r"równ\w*\s+lub\s+przekraczając\w*\s+progi\s+unijne|powyżej\s+progów\s+unijnych", low):
        return True
    if re.search(r"mniejsz\w*\s+niż\s+progi\s+unijne|poniżej\s+progów\s+unijnych", low):
        return False
    if ((odw_result or {}).get("ZamowieniePubliczne") or {}).get("numer_ted"):
        return True  # ogłoszenie w Dz.U. UE -> zamówienie unijne
    return None


def cel_przelewu(tytul):
    t = (tytul or "").lower()
    if re.search(r"\bwpis", t):
        return "wpis"
    if "skarbow" in t:
        return "oplata_skarbowa"
    return None


def _check(cid, nazwa, ok, opis, zrodla):
    return {"id": cid, "nazwa": nazwa, "status": "OK" if ok else "UWAGA", "opis": opis, "zrodla": zrodla}


def case_checks(docs):
    """Kontrole spójności sprawy z wynikiem OK / UWAGA (UWAGA także, gdy brak danych do porównania)."""
    checks = []
    odw = _first(docs, "odwolanie")
    pel = _all(docs, "pelnomocnictwo")
    prz = _first(docs, "przelew")
    odw_r = odw["result"] if odw else {}
    odw_party = (odw_r.get("Odwolujacy") or [{}])[0]
    moc = [m.get("data") or {} for d in pel for m in d["result"].get("mocodawca") or []]

    # 1. NIP odwołującego vs NIP mocodawcy
    nip_o = odw_party.get("nip")
    moc_nips = [m["nip"] for m in moc if m.get("nip")]
    src = ["odwolanie: Odwolujacy[0].nip", "pelnomocnictwo: mocodawca[].data.nip"]
    if not odw or not pel:
        checks.append(_check("nip", "NIP odwołującego = NIP mocodawcy", False,
                             "Brak " + ("odwołania" if not odw else "pełnomocnictwa") + " w sprawie — nie ma czego porównać.", src))
    elif not nip_o or not moc_nips:
        checks.append(_check("nip", "NIP odwołującego = NIP mocodawcy", False,
                             "Brak NIP " + ("w odwołaniu" if not nip_o else "mocodawcy w pełnomocnictwie") + " — nie można potwierdzić.", src))
    elif nip_o in moc_nips:
        checks.append(_check("nip", "NIP odwołującego = NIP mocodawcy", True,
                             f"NIP {nip_o} jest taki sam w odwołaniu i w pełnomocnictwie.", src))
    else:
        checks.append(_check("nip", "NIP odwołującego = NIP mocodawcy", False,
                             f"NIP odwołującego {nip_o} ≠ NIP mocodawcy {', '.join(moc_nips)}.", src))

    # 2. Kwota przelewu vs wpis od odwołania
    name = "Kwota przelewu = wpis od odwołania"
    wpis = wpis_z_tekstu(odw["text"]) if odw else None
    pb = (prz or {}).get("result", {}).get("przelew_bankowy") or {}
    kwota, cel = pb.get("kwota"), cel_przelewu(pb.get("tytul"))
    src = ["przelew: przelew_bankowy.kwota / tytul", "odwolanie: „Wpis od odwołania” w treści"]
    if not prz:
        checks.append(_check("wpis", name, False, "Brak potwierdzenia przelewu w sprawie.", src))
    elif kwota is None:
        checks.append(_check("wpis", name, False, "W potwierdzeniu przelewu nie ustalono kwoty.", src))
    elif cel == "wpis":
        if wpis is not None:
            checks.append(_check("wpis", name, abs(kwota - wpis) < 0.005,
                                 f"Przelew {fmt_pln(kwota)}, wpis wskazany w odwołaniu {fmt_pln(wpis)}"
                                 + ("." if abs(kwota - wpis) < 0.005 else " — kwoty się różnią."), src))
        else:
            prog = prog_unijny(odw["text"] if odw else "", odw_r)
            stawki = WPIS_OD_PROGOW if prog else WPIS_PONIZEJ_PROGOW if prog is False else WPIS_PONIZEJ_PROGOW + WPIS_OD_PROGOW
            opis_prog = {True: "od progów UE", False: "poniżej progów UE", None: "próg UE nieustalony"}[prog]
            ok = any(abs(kwota - s) < 0.005 for s in stawki)
            checks.append(_check("wpis", name, ok,
                                 f"Przelew {fmt_pln(kwota)}; odwołanie nie podaje kwoty wpisu, stawki ({opis_prog}): "
                                 + " / ".join(fmt_pln(s) for s in stawki) + ("." if ok else " — kwota spoza stawek."), src))
    elif cel == "oplata_skarbowa":
        checks.append(_check("wpis", name, False,
                             f"Przelew dotyczy opłaty skarbowej od pełnomocnictwa ({fmt_pln(kwota)}), nie wpisu"
                             + (f" (wpis wg odwołania: {fmt_pln(wpis)})" if wpis else "")
                             + " — w sprawie brak potwierdzenia uiszczenia wpisu.", src))
    else:
        checks.append(_check("wpis", name, False,
                             f"Tytuł przelewu ({fmt_pln(kwota)}) nie wskazuje wpisu od odwołania.", src))

    # 3. Numer postępowania w tytule przelewu
    nr = ((odw_r.get("ZamowieniePubliczne") or {}).get("numer_postepowania"))
    tyt = pb.get("tytul") or ""
    src = ["odwolanie: ZamowieniePubliczne.numer_postepowania", "przelew: przelew_bankowy.tytul"]
    if not nr or not tyt:
        checks.append(_check("nr_przelew", "Numer postępowania w tytule przelewu", False,
                             "Brak numeru postępowania w odwołaniu lub tytułu przelewu.", src))
    else:
        ok = re.sub(r"\s+", "", nr).lower() in re.sub(r"\s+", "", tyt).lower()
        checks.append(_check("nr_przelew", "Numer postępowania w tytule przelewu", ok,
                             f"{nr} " + ("występuje w tytule przelewu." if ok else "nie występuje w tytule przelewu."), src))

    # 4. Pełnomocnik z odwołania na liście pełnomocników
    peln_odw = (odw_party.get("pelnomocnik") or {}).get("imie_nazwisko")
    peln_list = [p.get("imie_nazwisko") for d in pel for p in d["result"].get("pelnomocnicy") or [] if p.get("imie_nazwisko")]
    src = ["odwolanie: Odwolujacy[0].pelnomocnik.imie_nazwisko", "pelnomocnictwo: pelnomocnicy[].imie_nazwisko"]
    if not peln_odw:
        opis = ("Brak odwołania w sprawie." if not odw else
                "Odwołanie nie wskazuje pełnomocnika, a w sprawie jest pełnomocnictwo." if pel else
                "Odwołanie nie wskazuje pełnomocnika i w sprawie nie ma pełnomocnictwa — spójne.")
        checks.append(_check("pelnomocnik", "Pełnomocnik z odwołania umocowany w pełnomocnictwie",
                             bool(odw and not pel), opis, src))
    elif not peln_list:
        checks.append(_check("pelnomocnik", "Pełnomocnik z odwołania umocowany w pełnomocnictwie", False,
                             f"{peln_odw} — brak listy pełnomocników do porównania.", src))
    else:
        ok = peln_odw in peln_list
        checks.append(_check("pelnomocnik", "Pełnomocnik z odwołania umocowany w pełnomocnictwie", ok,
                             f"{peln_odw} " + ("jest na liście pełnomocników." if ok else "nie występuje na liście: " + ", ".join(peln_list) + "."), src))

    # 5. Przystąpienie (jeśli jest w sprawie): ten sam numer postępowania
    for d in _all(docs, "przystapienie"):
        nr_p = d["result"].get("numer_postepowania")
        ok = bool(nr and nr_p and re.sub(r"\s+", "", nr).lower() == re.sub(r"\s+", "", nr_p).lower())
        checks.append(_check("przystapienie", "Przystąpienie dotyczy tego samego postępowania", ok,
                             f"Przystąpienie: {nr_p or 'brak numeru'}, odwołanie: {nr or 'brak numeru'}.",
                             ["przystapienie: numer_postepowania", "odwolanie: ZamowieniePubliczne.numer_postepowania"]))
    return checks


def case_summary(docs):
    """Jeden scalony podgląd sprawy; przy każdej wartości dokument źródłowy."""
    odw = _first(docs, "odwolanie")
    pel = _all(docs, "pelnomocnictwo")
    prz = _first(docs, "przelew")
    odw_r = odw["result"] if odw else {}
    party = (odw_r.get("Odwolujacy") or [None])[0]
    if party:
        odwolujacy = {k: party.get(k) for k in ("nazwa", "nip", "krs", "regon")}
        odwolujacy["zrodlo"] = odw["filename"]
    elif pel and (pel[0]["result"].get("mocodawca") or []):
        m = pel[0]["result"]["mocodawca"][0].get("data") or {}
        odwolujacy = {"nazwa": m.get("nazwa") or m.get("imie_nazwisko"), "nip": m.get("nip"),
                      "krs": m.get("krs"), "regon": None, "zrodlo": pel[0]["filename"] + " (mocodawca)"}
    else:
        odwolujacy = None
    zamawiajacy = [{"nazwa": z.get("nazwa"), "nip": z.get("nip"), "zrodlo": odw["filename"]}
                   for z in odw_r.get("Zamawiajacy") or []] if odw else []
    numer, numer_src = (odw_r.get("ZamowieniePubliczne") or {}).get("numer_postepowania"), odw["filename"] if odw else None
    if not numer:
        for d in _all(docs, "przystapienie"):
            if d["result"].get("numer_postepowania"):
                numer, numer_src = d["result"]["numer_postepowania"], d["filename"]
                break
    pb = (prz or {}).get("result", {}).get("przelew_bankowy") or {}
    wpis = wpis_z_tekstu(odw["text"]) if odw else None
    wpis_src = (odw["filename"] + " („Wpis od odwołania”)") if wpis is not None else None
    if wpis is None and cel_przelewu(pb.get("tytul")) == "wpis" and pb.get("kwota") is not None:
        wpis, wpis_src = pb["kwota"], prz["filename"] + " (przelew tytułem wpisu)"
    return {
        "odwolujacy": odwolujacy,
        "zamawiajacy": zamawiajacy,
        "numer_postepowania": {"wartosc": numer, "zrodlo": numer_src if numer else None},
        "kwota_wpisu": {"wartosc": wpis, "tekst": fmt_pln(wpis) if wpis is not None else None, "zrodlo": wpis_src},
        "przelew": {"kwota": pb.get("kwota"), "tekst": fmt_pln(pb["kwota"]) if pb.get("kwota") is not None else None,
                    "cel": cel_przelewu(pb.get("tytul")), "zrodlo": prz["filename"] if prz else None},
        "przystepujacy": [d["result"].get("przystepujacy") for d in _all(docs, "przystapienie")],
    }


def load_results_for_case(output_dir):
    import os
    results = {}
    for fname in os.listdir(output_dir):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(output_dir, fname), encoding="utf-8") as fh:
            data = json.load(fh)
        if "OdwolanieKIO" in data:
            results["odwolanie"] = data
        elif "mocodawca" in data:
            results["pelnomocnictwo"] = data
        elif "przelew_bankowy" in data:
            results["przelew"] = data
    return results
