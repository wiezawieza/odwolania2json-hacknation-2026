#!/bin/sh
# Odwołania2JSON — jedna komenda (Linux, Docker): <katalog aplikacji>/input -> <katalog aplikacji>/output.
# Na VM ewaluacyjnej: bash /opt/solution/run.sh   (szczegóły: RUN.md)
# Bez interakcji, kończy się sama. Obraz (Python 3.13 + Tesseract pol) buduje się tylko, gdy go brak.
# Exit code: 0 — wszystko przetworzone, 1 — co najmniej jeden plik nieczytelny / błąd walidacji
# (taki plik i tak ma swój JSON), 2 — brak folderu input. Dodatkowe argumenty idą do app.py (np. --no-llm).
set -eu
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE="${IMAGE:-odwolania2json}"
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "[run.sh] brak obrazu $IMAGE - buduję (jednorazowo, wymaga internetu)..."
    docker build -t "$IMAGE" "$APP_DIR"
fi
mkdir -p "$APP_DIR/input" "$APP_DIR/output"
exec docker run --rm -v "$APP_DIR":/app "$IMAGE" python app.py input output "$@"
