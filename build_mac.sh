#!/bin/bash
# Build "Lightroom Recover.app" on a Mac (Apple Silicon).
# Needs Python 3.12 from python.org (includes a working Tk for the window).
#   ./build_mac.sh            -> dist/Lightroom Recover.app and dist/LightroomRecover-mac.zip
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
"$PY" -c 'import tkinter' || { echo "This Python has no Tk. Install Python from python.org."; exit 1; }

"$PY" -m unittest discover tests

"$PY" -m venv .venv-mac
.venv-mac/bin/pip install --quiet --disable-pip-version-check pyinstaller==6.11.1
.venv-mac/bin/pyinstaller --noconfirm --clean --windowed \
    --name "Lightroom Recover" \
    --osx-bundle-identifier org.lrrecover.app \
    --distpath dist --workpath build-mac --specpath build-mac \
    lrrecover_gui.py

# ditto keeps the app bundle's signatures and metadata intact (plain zip can break it).
rm -f dist/LightroomRecover-mac.zip
ditto -c -k --keepParent "dist/Lightroom Recover.app" dist/LightroomRecover-mac.zip
echo "Built: dist/Lightroom Recover.app  (zip: dist/LightroomRecover-mac.zip)"
