#!/bin/bash
# Start Bridge43 on this Mac for a demo: the server (http://localhost:8000) and the menu bar app.
# WhatsApp messages and calls reach Bridge43 through the menu bar app (no tunnel or Twilio needed).
#
#   cd ~/Downloads/bridge43 && scripts/start_demo.sh
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

die() { echo "✗ $*" >&2; exit 1; }
[[ -x venv/bin/uvicorn ]] || die "no venv here: python3 -m venv venv && venv/bin/pip install -r requirements.txt"
[[ -f .env ]] || die "no .env file in $REPO (copy .env.example)"

pkill -f "uvicorn app.main:app" 2>/dev/null
sleep 1

if grep -qi "^REQUIRE_LOGIN=\(1\|true\|yes\|on\)" .env && ! grep -q "^STAFF_PASSWORD=..*" .env; then
  PW="$(openssl rand -base64 12 | tr -d '/+=' | cut -c1-12)"
  printf '\nSTAFF_PASSWORD=%s\n' "$PW" >> .env
  echo "✓ staff login created: username ps43, password $PW (saved in .env)"
fi

APP="$HOME/Applications/Bridge43 Recorder.app"
if [[ -d "$APP" ]]; then open "$APP"; echo "✓ menu bar app running (WhatsApp messages and calls)"
else echo "! menu bar app not installed yet: cd recorder && ./build_app.sh"; fi

(sleep 2; open "http://localhost:8000") &
echo "✓ server: http://localhost:8000  (Ctrl+C stops it)"
exec venv/bin/uvicorn app.main:app --port 8000 --reload --reload-dir app
