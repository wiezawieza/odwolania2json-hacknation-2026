# RUN — uruchomienie na maszynie ewaluacyjnej

Maszyna: Ubuntu 24.04, 2 vCPU, 16 GB RAM, bez GPU, Docker preinstalowany, Python 3.12, bez Tesseracta.
Aplikacja leży w `/opt/solution`, dokumenty wejściowe w `/opt/solution/input`, wyniki w `/opt/solution/output`.

## Komenda dla jury

Jedna komenda: bez interakcji, kończy się sama.

```bash
bash /opt/solution/run.sh
```

Komenda uruchamia gotowy obraz Docker `odwolania2json` (Python 3.13 i Tesseract OCR z językiem polskim są w obrazie,
na hoście nic nie trzeba instalować). Równoważne wywołanie bez skryptu:

```bash
docker run --rm -v /opt/solution:/app odwolania2json
```

Jeśli użytkownik nie należy do grupy `docker`, poprzedź komendę `sudo`.

### Co robi

- Przetwarza każdy PDF z `/opt/solution/input`, także w podfolderach spraw (`input/sprawa1/odwolanie.pdf`).
- Pracuje równolegle: 2 procesy robocze (tyle, ile vCPU ma VM), każdy dokument niezależnie. JSON-y są bajt w bajt
  takie same jak w jednym procesie (`bash /opt/solution/run.sh --workers 1`), tylko szybciej.
- Dla każdego pliku `X.pdf` zapisuje `X.json` w tym samym podfolderze `output/`. Nazwa jest identyczna z nazwą
  pliku wejściowego, zmienia się tylko końcówka (`potwierdzenie_przelewu.pdf` → `potwierdzenie_przelewu.json`).
- Dokument spoza obsługiwanych typów (odwołanie, pełnomocnictwo, potwierdzenie przelewu oraz zgłoszenie przystąpienia
  z konfiguracji) dostaje pusty JSON `{}` o tej samej nazwie. Pole, którego nie da się odczytać, ma wartość `null`
  (nigdy zgadywaną).
- Na końcu wypisuje podsumowanie i zapisuje raport `/opt/solution/output_raport.json` + `.csv` (poza `output/`,
  żeby w `output/` były wyłącznie JSON-y wynikowe): typ, status i czas każdego dokumentu.
- Exit code: `0` — wszystkie pliki przetworzone (dokument obcy → `{}` to poprawny wynik); `1` — co najmniej jeden plik
  nieczytelny lub z błędem walidacji (ten plik też ma swój JSON); `2` — brak folderu `input`.
- `run.sh` buduje obraz tylko wtedy, gdy go brakuje (wymaga to internetu). Na VM obraz jest zbudowany wcześniej, więc
  komenda tylko uruchamia przetwarzanie.

### Klucz API i tryb bez klucza

- Klucz API Mistral leży **wyłącznie na serwerze** w pliku `/opt/solution/.env` (nigdy w repozytorium;
  `.env` jest w `.gitignore` i `.dockerignore`). Kontener czyta go z zamontowanego katalogu `/opt/solution`.
- Bez pliku `.env` (albo z `--no-llm`: `bash /opt/solution/run.sh --no-llm`) aplikacja działa w pełni
  w trybie deterministycznym (regex + heurystyka). Na 210 dokumentach treningowych LLM nie jest wywoływany ani razu,
  więc wynik jest taki sam z kluczem i bez niego.

### Czas i wynik w tym środowisku

- PDF tekstowy: ok. 0,01 s. Skany na 2 rdzeniach (ten obraz przypięty do 2 rdzeni laptopa i7-6700HQ z 2016 r., OCR
  od zera): 12 skanów (28 stron) w 85 s, czyli 7,1 s na skan, więc **150 samych skanów ≈ 18 min** przy limicie 60 min.
  Gdyby 2 vCPU VM miały moc tylko jednego rdzenia: ok. 33 min. Przed zmianą (1 proces, Tesseract z OpenMP) było to
  ok. 78 min. Szczegóły: [README.md](README.md#zmierzony-czas-przetwarzania).
- F1 na danych treningowych w tym obrazie (Tesseract 5.5.0): PDF-y tekstowe 1,0000, skany 0,9952, łącznie 0,9994
  ([EVAL.md](EVAL.md)), tak samo przy 1 i przy 2 workerach.

## Przygotowanie VM (zespół, przed odcięciem dostępu o 11:00)

```bash
# 1. Kod do /opt/solution: repozytorium jest publiczne, zwykły git clone bez logowania
#    (input/ i output/ są w repozytorium jako puste foldery).
git clone --depth 1 https://github.com/wiezawieza/odwolania2json-hacknation-2026.git /tmp/odw
sudo mkdir -p /opt/solution && sudo cp -a /tmp/odw/. /opt/solution/

# 2. Klucz API tylko na serwerze (podmień <KLUCZ>)
echo 'LLM_API_KEY=<KLUCZ>' | sudo tee /opt/solution/.env >/dev/null && sudo chmod 600 /opt/solution/.env

# 3. Obraz Docker (Python 3.13 + tesseract-ocr + tesseract-ocr-pol), wymaga internetu
sudo docker build -t odwolania2json /opt/solution

# 4. Próba generalna na publicznych danych treningowych UZP (sprawa tekstowa + 30 skanów), potem sprzątanie
git clone --depth 1 https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026.git /tmp/uzp
sudo cp -r /tmp/uzp/dane/przyklad_01 /tmp/uzp/dane/skany /opt/solution/input/
sudo bash /opt/solution/run.sh; ls -R /opt/solution/output | head -20
sudo rm -rf /opt/solution/input/* /opt/solution/output/* /opt/solution/output_raport.*
```

Linia „Gotowe” z próby podaje czas zegarowy na samej VM. Ten czas podzielony przez 30 i pomnożony przez 150 to prognoza
dla 150 samych skanów (sprawa tekstowa trwa ułamek sekundy).

## GUI na VM (opcjonalnie, port 8000)

```bash
sudo docker run -d --rm --name odwolania2json-gui -p 8000:8000 -v /opt/solution:/app odwolania2json \
    python app.py --serve --host 0.0.0.0 --port 8000
```

Interfejs: `http://<adres-VM>:8000`. Zatrzymanie: `sudo docker stop odwolania2json-gui`.

## Wariant bez Dockera (Python 3.12 z VM)

Kod działa na Pythonie 3.12 i 3.13 (sprawdzone uruchomieniem na obu wersjach). Tesseracta na VM nie ma, więc trzeba go doinstalować:

```bash
sudo apt-get update && sudo apt-get install -y tesseract-ocr tesseract-ocr-pol python3-venv
cd /opt/solution && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python app.py input output
```

Bez Tesseracta aplikacja się nie wysypuje. PDF-y tekstowe są przetwarzane normalnie, a skan bez warstwy tekstowej
dostaje `{}` (jego typu nie da się rozpoznać bez OCR) i status `ERR` w raporcie (exit code `1`).
