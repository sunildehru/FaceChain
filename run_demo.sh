#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

echo "========================================================"
echo "        FaceChain Biometric Verification Pipeline"
echo "========================================================"
echo "Working Directory: $(pwd)"
echo ""

mkdir -p temp/scraped

if [ ! -d "venv" ]; then
    echo "[ERROR] Virtual environment not found in 'venv' folder."
    echo "Please create the virtual environment and install requirements:"
    echo "  python3 -m venv venv"
    echo "  ./venv/bin/pip install -r requirements.txt"
    exit 1
fi

echo "[1/3] Activating virtual environment..."
source venv/bin/activate

echo "[2/3] Verifying smart contract compilation..."
python3 src/chain/compile.py

echo ""
echo "[3/3] Launching FaceChain Server on http://127.0.0.1:8000 ..."
echo "Press Ctrl+C to stop the server."
echo ""
python3 -m uvicorn src.api:app --reload --host 127.0.0.1 --port 8000
