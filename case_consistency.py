"""Spójność sprawy (hasło UZP: 'Zrozum sprawę. Nie tylko dokument.').

Po ekstrakcji 3 dokumentów jednej sprawy porównujemy pola, które muszą
się zgadzać między dokumentami, i uzupełniamy braki z innych dokumentów
tej samej sprawy. Nie wymyślamy danych — uzupełniamy tylko tym, co jest
w innym dokumencie tej samej sprawy. Niezgodności trafiają do logu.
"""

import json


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
