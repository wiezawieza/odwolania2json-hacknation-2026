# Odwołania2JSON — obraz do uruchomienia bez instalacji czegokolwiek na hoście:
# Python 3.13 (wymóg UZP) + Tesseract OCR z językiem polskim (na VM ewaluacyjnej go nie ma).
FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-pol poppler-utils \
    && rm -rf /var/lib/apt/lists/* \
    && tesseract --list-langs | grep -qx pol
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Kod aplikacji (bez .env, danych i wyników — patrz .dockerignore). Przy uruchomieniu z RUN.md
# katalog /opt/solution jest montowany jako /app: input/, output/ i .env są czytane z serwera.
COPY . .
RUN mkdir -p input output
CMD ["python", "app.py", "input", "output"]
