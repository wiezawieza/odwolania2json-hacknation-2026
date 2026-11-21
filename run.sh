#!/bin/sh
# Odwołania2JSON — uruchomienie na Linux/macOS
# Wymaga: Python 3.13+ oraz pip install -r requirements.txt
set -e
cd "$(dirname "$0")"
python3 app.py input output
