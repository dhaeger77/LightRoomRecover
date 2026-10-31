#!/bin/bash
# Build "Lightroom Recover.app".
# With Python 3.12 from python.org (a "universal2" build), the app runs on both
# Apple Silicon and Intel Macs. With any other Python it only runs on this Mac's chip.
#   ./build_mac.sh  -> dist/Lightroom Recover.app and dist/LightroomRecover-mac.zip
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
"$PY" -c 'import tkinter' || { echo "This Python has no Tk. Install Python from python.org."; exit 1; }

REAL_PY=$("$PY" -c 'import os, sys; print(os.path.realpath(sys.executable))')
PY_ARCHS=$(lipo -archs "$REAL_PY" 2>/dev/null || uname -m)
if [[ " $PY_ARCHS " == *" arm64 "* && " $PY_ARCHS " == *" x86_64 "* ]]; then
    TARGET=(--target-arch universal2)
    echo "Building a universal app (Apple Silicon + Intel)."
else
    TARGET=()
    echo "WARNING: $REAL_PY is $PY_ARCHS only, so the app will only run on that chip."
    echo "         Install Python from python.org for a universal app."
fi

"$PY" -m unittest discover tests

rm -rf .venv-mac
"$PY" -m venv .venv-mac
.venv-mac/bin/pip install --quiet --disable-pip-version-check pyinstaller==6.11.1
.venv-mac/bin/pyinstaller --noconfirm --clean --windowed "${TARGET[@]}" \
    --name "Lightroom Recover" \
    --osx-bundle-identifier org.lrrecover.app \
    --distpath dist --workpath build-mac --specpath build-mac \
    lrrecover_gui.py

echo "App architectures: $(lipo -archs "dist/Lightroom Recover.app/Contents/MacOS/Lightroom Recover")"

# ditto keeps the app bundle's signatures and metadata intact (plain zip can break it).
rm -f dist/LightroomRecover-mac.zip
ditto -c -k --keepParent "dist/Lightroom Recover.app" dist/LightroomRecover-mac.zip
echo "Built: dist/Lightroom Recover.app  (zip: dist/LightroomRecover-mac.zip)"
