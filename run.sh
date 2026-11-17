#!/usr/bin/env bash
# Signal Studio — your LinkedIn agent. Usage: ./run.sh   then open http://localhost:8099
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${STUDIO_PORT:-8099}"
