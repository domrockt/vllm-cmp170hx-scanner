#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m py_compile app.py
echo "Dashboard: http://0.0.0.0:8080"
exec python3 app.py
