#!/bin/bash
# Build "Lightroom Recover.app" for the Mac this runs on (Apple Silicon or Intel).
# Needs Python 3.12 from python.org (includes a working Tk for the window).
#   ./build_mac.sh  -> dist/Lightroom Recover.app and
#                      dist/LightroomRecover-mac-AppleSilicon.zip  (or -Intel.zip)
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
"$PY" -c 'import tkinter' || { echo "This Python has no Tk. Install Python from python.org."; exit 1; }

case "$(uname -m)" in
    arm64)  ZIP=LightroomRecover-mac-AppleSilicon.zip ;;
    x86_64) ZIP=LightroomRecover-mac-Intel.zip ;;
    *)      ZIP="LightroomRecover-mac-$(uname -m).zip" ;;
esac

"$PY" -m unittest discover tests

"$PY" -m venv .venv-mac
.venv-mac/bin/pip install --quiet --disable-pip-version-check pyinstaller==6.11.1
.venv-mac/bin/pyinstaller --noconfirm --clean --windowed \
    --name "Lightroom Recover" \
    --osx-bundle-identifier org.lrrecover.app \
    --distpath dist --workpath build-mac --specpath build-mac \
    lrrecover_gui.py

# ditto keeps the app bundle's signatures and metadata intact (plain zip can break it).
rm -f "dist/$ZIP"
ditto -c -k --keepParent "dist/Lightroom Recover.app" "dist/$ZIP"
echo "Built: dist/Lightroom Recover.app  (zip: dist/$ZIP)"
