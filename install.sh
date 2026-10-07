#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "Best install: run this on the same PC as vLLM and the CMP 170HX."
echo "Checking local requirements..."
missing=()
command -v python3 >/dev/null || missing+=(python3)
python3 - <<'PY' || missing+=(python3.9+)
import sys
raise SystemExit(0 if sys.version_info >= (3, 9) else 1)
PY
command -v nvidia-smi >/dev/null || missing+=(nvidia-smi)
command -v dmidecode >/dev/null || missing+=(dmidecode)
command -v lspci >/dev/null || missing+=(pciutils)
if ((${#missing[@]})); then
  echo "Missing optional or required tools: ${missing[*]}"
  echo "The installer can install only small read tools: dmidecode and pciutils."
  echo "It will not install NVIDIA drivers, CUDA or vLLM."
  read -r -p "Install missing system read tools now? [y/N] " answer
  if [[ "${answer}" == "y" || "${answer}" == "Y" ]]; then
    if command -v apt-get >/dev/null; then
      sudo apt-get update
      sudo apt-get install -y dmidecode pciutils
    else
      echo "No supported package manager found. Install dmidecode and pciutils manually."
    fi
  fi
else
  echo "Requirements found."
fi

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
