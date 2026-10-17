#!/bin/bash
# Start everything for a demo in one go (Mac):
#   cloudflared tunnel -> its address saved to .env -> server -> call recorder
# The Twilio webhook address is printed and copied to the clipboard. Ctrl+C stops everything.
#
#   cd ~/Downloads/bridge43 && scripts/start_demo.sh
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" || exit 1
LOGS="$REPO/data/logs"
mkdir -p "$LOGS"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

die() { echo "✗ $*" >&2; exit 1; }
[[ -x venv/bin/uvicorn ]] || die "no venv here: python3 -m venv venv && venv/bin/pip install -r requirements.txt"
[[ -f .env ]] || die "no .env file in $REPO"
command -v cloudflared >/dev/null || die "cloudflared missing: brew install cloudflared"
RECORDER="$REPO/recorder/.build/release/bridge43-recorder"

# Stop earlier copies (manual runs, background services, the brew cloudflared service)
pkill -f "uvicorn app.main:app" 2>/dev/null
pkill -f "cloudflared tunnel --url" 2>/dev/null
pkill -f "bridge43-recorder" 2>/dev/null
brew services stop cloudflared >/dev/null 2>&1
for l in server tunnel recorder; do launchctl bootout "gui/$(id -u)/com.bridge43.$l" 2>/dev/null; done
sleep 1

PIDS=()
cleanup() { echo; echo "Stopping…"; kill "${PIDS[@]}" 2>/dev/null; wait 2>/dev/null; echo "Stopped."; }
trap cleanup EXIT INT TERM

set_env() {
  if grep -q "^$1=" .env; then sed -i '' "s|^$1=.*|$1=$2|" .env; else printf '\n%s=%s\n' "$1" "$2" >> .env; fi
}

# 1. Tunnel
echo "… starting the tunnel"
: > "$LOGS/tunnel.log"
cloudflared tunnel --no-autoupdate --url http://localhost:8000 > "$LOGS/tunnel.log" 2>&1 &
PIDS+=($!)
URL=""
for _ in $(seq 1 40); do
  URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOGS/tunnel.log" | head -1)"
  [[ -n "$URL" ]] && break
  sleep 0.5
done
[[ -n "$URL" ]] || die "the tunnel did not start (network blocking it? try a hotspot). Log: $LOGS/tunnel.log"
echo "✓ tunnel: $URL"

# 2. .env: public address (+ a staff password if there is none)
set_env PUBLIC_BASE_URL "$URL"
if ! grep -q "^STAFF_PASSWORD=..*" .env; then
  PW="$(openssl rand -base64 12 | tr -d '/+=' | cut -c1-12)"
  set_env STAFF_PASSWORD "$PW"
  echo "✓ staff login created: username ps43, password $PW (saved in .env)"
fi

# 3. Server
echo "… starting the server"
venv/bin/uvicorn app.main:app --port 8000 --reload --reload-dir app > "$LOGS/server.log" 2>&1 &
PIDS+=($!)
for _ in $(seq 1 30); do curl -s -m 2 http://127.0.0.1:8000/health >/dev/null && break; sleep 0.5; done
curl -s -m 2 http://127.0.0.1:8000/health >/dev/null || die "the server did not start. Log: $LOGS/server.log"
echo "✓ server: http://localhost:8000"

# 4. Call recorder
if [[ -x "$RECORDER" ]]; then
  "$RECORDER" > "$LOGS/recorder.log" 2>&1 &
  PIDS+=($!)
  echo "✓ call recorder running"
else
  echo "! call recorder not built (cd recorder && swift build -c release): skipping calls"
fi

# 5. Twilio
HOOK="$URL/webhooks/twilio/messaging"
printf '%s' "$HOOK" | pbcopy 2>/dev/null
sleep 3
if curl -s -m 10 "$URL/health" >/dev/null; then echo "✓ reachable from the internet"; else echo "! not reachable from the internet yet (give it a few seconds)"; fi
cat <<EOF

────────────────────────────────────────────────────────────────────────────
 Paste into Twilio (already on your clipboard):
   Console → Messaging → Try it out → Send a WhatsApp message → Sandbox settings
   "When a message comes in":  $HOOK    Method: POST   → Save

 Inbox: http://localhost:8000      Logs: $LOGS
 Leave this window open. Ctrl+C stops everything.
────────────────────────────────────────────────────────────────────────────

EOF
open "http://localhost:8000" 2>/dev/null
tail -n 0 -f "$LOGS/server.log" "$LOGS/recorder.log" 2>/dev/null &
PIDS+=($!)
wait
