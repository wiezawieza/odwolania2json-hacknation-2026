# Wynik ewaluacji F1 (definicja UZP, Discord 11:26)

Przebieg: predykcje `output_docker`, silnik OCR: tesseract 5.5.0

Dokumentów porównanych: 210, brak predykcji dla: 0
TP=2340 TN=362 FP=2 FN=1
**F1 łączny: 0.9994**

## Dokumenty negatywne (oczekiwany pusty JSON {})
- poprawnie puste: 16/16, FP z niepustych negatywów: 0
- F1 łączny z negatywami: 0.9994

## F1 per typ dokumentu
- odwolanie: F1=0.9989 (TP=1390 FP=2 FN=1 TN=168)
- pelnomocnictwo: F1=1.0000 (TP=536 FP=0 FN=0 TN=188)
- przelew: F1=1.0000 (TP=414 FP=0 FN=0 TN=6)

## F1 wg rodzaju PDF-a (raport wsadowy `output_docker_raport.json`)
- silnik OCR w tym przebiegu: tesseract 5.5.0
- PDF tekstowy: F1=1.0000 (dokumentów=180, TP=2032 FP=0 FN=0 TN=284)
- skan (OCR): F1=0.9952 (dokumentów=30, TP=308 FP=2 FN=1 TN=78)
- Wynik skanów zależy od wersji silnika OCR, nie od reguł ekstrakcji: ten sam kod i ten sam model `pol.traineddata` dają różny tekst skanu na Tesseracie 5.4.0, 5.5.0 i 5.3.4. Porównanie: TECHNICAL.md, sekcja „OCR: wersja silnika a wynik”.

## Wszystkie błędy (dokument | pole | rodzaj | oczekiwane | otrzymane)
- skany/odwolania/0005/0005.json | `Zamawiajacy[0].nazwa` | FP+FN | oczekiwane: 'Regionalne Centrum Zdrowia Publicznego w Dębnicy Górnej' | otrzymane: 'reprezentowany przez pełnomocnika:'
- skany/odwolania/0005/0005.json | `Zamawiajacy[0].regon` | FP | oczekiwane: None | otrzymane: '999749508'

## Rachunki bankowe a prefiks kraju (UZP, Discord 21:17 i 23:00)
Numer rachunku zapisujemy dokładnie jak w dokumencie, usuwając wyłącznie białe znaki — z prefiksem kraju (np. `PL`), jeśli dokument go podaje.
- pól rachunków: 140; identycznych z wzorcem: 43 (w tym z prefiksem we wzorcu: 2); różnica wyłącznie w prefiksie kraju: 96; inna różnica: 1
- **Znany błąd wzorców UZP:** w 96 polach dokument PDF zawiera prefiks `PL`, a wzorcowy JSON UZP go nie ma. UZP potwierdziło (21:17, 23:00), że prefiks ma zostać, dlatego wynik go zachowuje — tej poprawki nie cofamy. Wzorce, które mają prefiks, zgadzają się z wynikiem co do znaku.
- F1 wg definicji UZP się nie zmienia: numer z prefiksem (`PL` + 26 cyfr) ma podobieństwo 0,963 ≥ 0,80 do wzorca bez prefiksu. Przy ścisłym porównaniu (bez progu 0,80) te 96 pól liczyłoby się jako FP+FN wyłącznie z powodu błędu wzorców.
- inne różnice (np. błędnie odczytana cyfra w skanie), liczone zwykłą metryką UZP:
  - skany/potwierdzenia/0007/0007.json przelew_bankowy.z_rachunku: wynik='66109010980367304538982666', wzorzec='66109010980387304538982666'

## Najgorsze pola (liczba błędów FP/FN/FP+FN)
- Zamawiajacy[0].nazwa: błędy=1 (TP=69 FP=0 FN=0 FP+FN=1 TN=0)
- Zamawiajacy[0].regon: błędy=1 (TP=62 FP=1 FN=0 FP+FN=0 TN=7)

## F1 per pole (top 30 pól)
- mocodawca[1].data.imie_nazwisko: F1=0.0000 (TP=0 FP=0 FN=0)
- pelnomocnicy[2].pesel: F1=0.0000 (TP=0 FP=0 FN=0)
- Zamawiajacy[0].nazwa: F1=0.9857 (TP=69 FP=1 FN=1)
- Zamawiajacy[0].regon: F1=0.9920 (TP=62 FP=1 FN=0)
- OdwolanieKIO.czynnosc_dzien: F1=1.0000 (TP=70 FP=0 FN=0)
- OdwolanieKIO.czynnosc_opis: F1=1.0000 (TP=70 FP=0 FN=0)
- OdwolanieKIO.czynnosc_powod_id: F1=1.0000 (TP=70 FP=0 FN=0)
- OdwolanieKIO.data: F1=1.0000 (TP=55 FP=0 FN=0)
- OdwolanieKIO.miejscowosc: F1=1.0000 (TP=55 FP=0 FN=0)
- Odwolujacy[0].krs: F1=1.0000 (TP=64 FP=0 FN=0)
- Odwolujacy[0].nazwa: F1=1.0000 (TP=70 FP=0 FN=0)
- Odwolujacy[0].nip: F1=1.0000 (TP=70 FP=0 FN=0)
- Odwolujacy[0].pelnomocnik.imie_nazwisko: F1=1.0000 (TP=69 FP=0 FN=0)
- Odwolujacy[0].pelnomocnik.mail: F1=1.0000 (TP=69 FP=0 FN=0)
- Odwolujacy[0].pelnomocnik.nazwa: F1=1.0000 (TP=69 FP=0 FN=0)
- Odwolujacy[0].przez-pelnomocnika: F1=1.0000 (TP=70 FP=0 FN=0)
- Odwolujacy[0].regon: F1=1.0000 (TP=66 FP=0 FN=0)
- Zamawiajacy[0].krs: F1=1.0000 (TP=55 FP=0 FN=0)
- Zamawiajacy[0].nip: F1=1.0000 (TP=68 FP=0 FN=0)
- Zamawiajacy[0].prowadzacy_postepowanie: F1=1.0000 (TP=4 FP=0 FN=0)
- Zamawiajacy[1].krs: F1=1.0000 (TP=3 FP=0 FN=0)
- Zamawiajacy[1].nazwa: F1=1.0000 (TP=4 FP=0 FN=0)
- Zamawiajacy[1].nip: F1=1.0000 (TP=3 FP=0 FN=0)
- Zamawiajacy[1].prowadzacy_postepowanie: F1=1.0000 (TP=4 FP=0 FN=0)
- Zamawiajacy[1].regon: F1=1.0000 (TP=4 FP=0 FN=0)
- ZamowieniePubliczne.numer_bzp: F1=1.0000 (TP=53 FP=0 FN=0)
- ZamowieniePubliczne.numer_postepowania: F1=1.0000 (TP=70 FP=0 FN=0)
- ZamowieniePubliczne.numer_ted: F1=1.0000 (TP=54 FP=0 FN=0)
- ZamowieniePubliczne.tytul: F1=1.0000 (TP=70 FP=0 FN=0)
- mocodawca[0].data.imie_nazwisko: F1=1.0000 (TP=5 FP=0 FN=0)
