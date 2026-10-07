#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
echo "Best install: run this on the same PC as vLLM and the CMP 170HX."
echo "Press Enter to scan only this PC, or enter the vLLM host/IP."
read -r -p "vLLM host [local]: " host
if [[ -n "${host}" && "${host}" != "local" ]]; then
  printf 'VLLM_HOST=%s\n' "$host" > .env
  echo "Saved remote vLLM host in .env"
else
  rm -f .env
  echo "Using local auto-discovery."
fi
python3 -m py_compile app.py
echo "Dashboard: http://0.0.0.0:8080"
exec python3 app.py
