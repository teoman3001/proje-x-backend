#!/usr/bin/env bash
set -euo pipefail
# Ngrok authtoken: https://dashboard.ngrok.com/get-started/your-authtoken
TOKEN="${NGROK_AUTHTOKEN:-${1:-}}"
if [ -z "$TOKEN" ]; then
  echo "NGROK_AUTHTOKEN tanımlı değil."
  echo "1) https://dashboard.ngrok.com/get-started/your-authtoken adresinden token alın"
  echo "2) export NGROK_AUTHTOKEN='YOUR_TOKEN'"
  echo "   veya: $0 YOUR_TOKEN"
  exit 1
fi
ngrok config add-authtoken "$TOKEN"
exec ngrok http 8000
