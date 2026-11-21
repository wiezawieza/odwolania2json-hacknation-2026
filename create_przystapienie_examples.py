import os
import json
import textwrap
import fitz

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_FILE = "C:/Windows/Fonts/arial.ttf"

def make_pdf(text, pdf_path):
    os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    font = fitz.Font(fontfile=FONT_FILE)
    page.insert_font(fontname="arial", fontfile=FONT_FILE)
    y = 50
    for raw_line in text.splitlines():
        if not raw_line.strip():
            y += 14
            continue
        # Wrap long lines so text fits on the page
        wrapped = textwrap.wrap(raw_line, width=75) if len(raw_line) > 75 else [raw_line]
        for line in wrapped:
            page.insert_text(fitz.Point(50, y), line, fontname="arial", fontsize=10)
            y += 14
            if y > 800:
                page = doc.new_page(width=595, height=842)
                page.insert_font(fontname="arial", fontfile=FONT_FILE)
                y = 50
    doc.save(pdf_path)
    doc.close()

EXAMPLES = [
    {
        "dir": "examples/przystapienie/przyklad_01",
        "json": {
            "przystepujacy": {
                "nazwa": "Vistula Budownictwo Sp. z o.o.",
                "nip": "5252819402",
                "krs": "0000845192",
                "regon": "386129481"
            },
            "numer_postepowania": "ZP/45/2026",
            "strona": "po stronie odwołującego",
            "data": "2026-04-18",
            "podpisujacy": [
                {
                    "imie_nazwisko": "Michał Kowalczyk"
                }
            ]
        },
        "text": """Warszawa, dnia 18 kwietnia 2026 r.

Do:
Prezes Krajowej Izby Odwoławczej
ul. Postępu 17a
02-676 Warszawa

Przystępujący:
Vistula Budownictwo Sp. z o.o.
ul. Marszałkowska 10
00-001 Warszawa
NIP: 5252819402
KRS: 0000845192
REGON: 386129481

Zamawiający:
Miejskie Przedsiębiorstwo Wodociągów i Kanalizacji m.st. Warszawy S.A.
pl. Starynkiewicza 5
02-015 Warszawa

Odwołujący:
Budimex Inżynieria Sp. z o.o.
ul. Siedmiogrodzka 9
01-204 Warszawa


ZGŁOSZENIE PRZYSTĄPIENIA DO POSTĘPOWANIA ODWOŁAWCZEGO

Działając w imieniu wykonawcy Vistula Budownictwo Sp. z o.o., na podstawie art. 524 ust. 1 ustawy z dnia 11 września 2019 r. – Prawo zamówień publicznych (Dz. U. z 2023 r. poz. 1605 ze zm.), niniejszym zgłaszam przystąpienie do postępowania odwoławczego po stronie odwołującego w sprawie wywołanej odwołaniem wniesionym przez Budimex Inżynieria Sp. z o.o.

Postępowanie dotyczy zamówienia publicznego na: Modernizacja stacji uzdatniania wody.
Numer postępowania: ZP/45/2026.

Wykonawca wskazuje, że posiada interes w uzyskaniu rozstrzygnięcia na korzyść strony, do której przystępuje, gdyż uwzględnienie odwołania umożliwi przywrócenie oferty odwołującego i prawidłową ocenę ofert.

Kopia niniejszego zgłoszenia przystąpienia została przesłana Zamawiającemu oraz Odwołującemu zgodnie z art. 524 ust. 1 Pzp.


W imieniu Przystępującego:

..................................................
Michał Kowalczyk
Prezes Zarządu
"""
    },
    {
        "dir": "examples/przystapienie/przyklad_02",
        "json": {
            "przystepujacy": {
                "nazwa": "Solaris Infrastruktura S.A.",
                "nip": "7792415893",
                "krs": "0000712944",
                "regon": "369812450"
            },
            "numer_postepowania": "DAZ/112/2026",
            "strona": "po stronie zamawiającego",
            "data": "2026-05-10",
            "podpisujacy": [
                {
                    "imie_nazwisko": "Krzysztof Majewski"
                }
            ]
        },
        "text": """Gdańsk, dnia 10 maja 2026 r.

Do:
Prezes Krajowej Izby Odwoławczej
ul. Postępu 17a
02-676 Warszawa

Przystępujący:
Solaris Infrastruktura S.A.
al. Grunwaldzka 472
80-309 Gdańsk
NIP: 7792415893
KRS: 0000712944
REGON: 369812450

Zamawiający:
Zarząd Dróg i Zieleni w Gdańsku
ul. Partyzantów 36
80-254 Gdańsk

Odwołujący:
Trakcja Torowa Sp. z o.o.
ul. Hutnicza 15
81-061 Gdynia


ZGŁOSZENIE PRZYSTĄPIENIA DO POSTĘPOWANIA ODWOŁAWCZEGO

Działając w imieniu Solaris Infrastruktura S.A., na podstawie art. 524 ustawy z dnia 11 września 2019 r. Prawo zamówień publicznych, zgłaszam przystąpienie do postępowania odwoławczego po stronie zamawiającego.

Postępowanie o udzielenie zamówienia publicznego: Rozbudowa układu drogowego węzła integracyjnego.
Numer referencyjny postępowania nr DAZ/112/2026.

Przystępujący, którego oferta została wybrana jako najkorzystniejsza w przedmiotowym postępowaniu, wnosi o oddalenie odwołania w całości jako bezzasadnego.


W imieniu Przystępującego:

..................................................
Krzysztof Majewski
Wiceprezes Zarządu
"""
    },
    {
        "dir": "examples/przystapienie/przyklad_03",
        "json": {
            "przystepujacy": {
                "nazwa": "Novatech Systemy Sp. z o.o.",
                "nip": "9512398741",
                "krs": "0000654321",
                "regon": "147852369"
            },
            "numer_postepowania": "BZP.271.88.2026",
            "strona": "po stronie zamawiającego",
            "data": "2026-06-02",
            "podpisujacy": [
                {
                    "imie_nazwisko": "Tomasz Adamski"
                },
                {
                    "imie_nazwisko": "Piotr Wróblewski"
                }
            ]
        },
        "text": """Poznań, dnia 2 czerwca 2026 r.

Do:
Prezes Krajowej Izby Odwoławczej
ul. Postępu 17a
02-676 Warszawa

Przystępujący:
Wykonawcy wspólnie ubiegający się o zamówienie (Konsorcjum firm):
1) Lider Konsorcjum: Novatech Systemy Sp. z o.o.
ul. Prosta 5, 00-850 Warszawa
NIP: 9512398741
KRS: 0000654321
REGON: 147852369
2) Partner Konsorcjum: Elektro-Bud Sp. z o.o.
ul. Kolejowa 12, 01-234 Warszawa
NIP: 5213698521, KRS: 0000456789, REGON: 142536789

Zamawiający:
Wojewódzki Szpital Zespolony w Poznaniu
ul. Juraszów 7-19
60-479 Poznań


ZGŁOSZENIE PRZYSTĄPIENIA DO POSTĘPOWANIA ODWOŁAWCZEGO

Działając w imieniu Wykonawców wspólnie ubiegających się o udzielenie zamówienia publicznego (Konsorcjum w składzie wskazanym powyżej), na podstawie art. 524 ust. 1 ustawy Prawo zamówień publicznych, zgłaszamy przystąpienie do postępowania odwoławczego po stronie zamawiającego.

Sprawa dotyczy postępowania na: Dostawa i wdrożenie zintegrowanego systemu szpitalnego.
Znak sprawy: BZP.271.88.2026.

Konsorcjum wykonawców wykazuje interes w rozstrzygnięciu odwołania na korzyść Zamawiającego i wnosi o oddalenie odwołania wniesionego przez konkurencyjnego wykonawcę.


Za Lidera Konsorcjum działającego w imieniu Konsorcjum:

..................................................
Tomasz Adamski
Członek Zarządu

..................................................
Piotr Wróblewski
Wiceprezes Zarządu
"""
    }
]

def main():
    for ex in EXAMPLES:
        d = os.path.join(BASE_DIR, ex["dir"])
        os.makedirs(d, exist_ok=True)
        pdf_path = os.path.join(d, "przystapienie.pdf")
        json_path = os.path.join(d, "przystapienie.json")
        make_pdf(ex["text"], pdf_path)
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(ex["json"], fh, ensure_ascii=False, indent=2)
        print(f"Utworzono: {pdf_path} oraz {json_path}")

if __name__ == "__main__":
    main()
