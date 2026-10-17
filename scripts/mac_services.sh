#!/bin/bash
# Run Bridge43 permanently on this Mac: the server, a fixed-address ngrok tunnel and the call recorder
# start at login, restart if they crash, and the Mac is kept awake.
#
#   scripts/mac_services.sh install <your-ngrok-domain>   e.g.  scripts/mac_services.sh install bridge43.ngrok-free.app
#   scripts/mac_services.sh status
#   scripts/mac_services.sh logs                           follow all logs
#   scripts/mac_services.sh restart
#   scripts/mac_services.sh uninstall
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
LOGS="$HOME/Library/Logs/Bridge43"
UID_NUM="$(id -u)"
LABELS=(com.bridge43.server com.bridge43.tunnel com.bridge43.recorder com.bridge43.awake)
PATH_ENV="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

die() { echo "✗ $*" >&2; exit 1; }
ok() { echo "✓ $*"; }

plist() {  # label, program args..., written to ~/Library/LaunchAgents/<label>.plist
  local label="$1"; shift
  local args=""
  for a in "$@"; do args+="    <string>$(printf '%s' "$a" | sed 's/&/\&amp;/g; s/</\&lt;/g')</string>"$'\n'; done
  cat > "$AGENTS/$label.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
$args  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$PATH_ENV</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$LOGS/${label#com.bridge43.}.log</string>
  <key>StandardErrorPath</key><string>$LOGS/${label#com.bridge43.}.log</string>
</dict>
</plist>
EOF
}

set_env() {  # key, value: replace or append a line in .env
  local key="$1" value="$2" file="$REPO/.env"
  touch "$file"
  if grep -q "^$key=" "$file"; then
    sed -i '' "s|^$key=.*|$key=$value|" "$file"
  else
    printf '\n%s=%s\n' "$key" "$value" >> "$file"
  fi
}

load() { launchctl bootout "gui/$UID_NUM/$1" 2>/dev/null || true; launchctl bootstrap "gui/$UID_NUM" "$AGENTS/$1.plist"; }

install() {
  local domain="${1:-}"
  [[ -n "$domain" ]] || die "give your free ngrok domain: scripts/mac_services.sh install your-name.ngrok-free.app
   (get one at dashboard.ngrok.com → Domains → New Domain)"
  domain="${domain#https://}"; domain="${domain%/}"

  [[ -x "$REPO/venv/bin/uvicorn" ]] || die "no venv: run  python3 -m venv venv && venv/bin/pip install -r requirements.txt"
  [[ -f "$REPO/.env" ]] || die "no .env file in $REPO"
  command -v ffmpeg >/dev/null || [[ -x /opt/homebrew/bin/ffmpeg ]] || die "ffmpeg missing: brew install ffmpeg"
  local ngrok_bin; ngrok_bin="$(command -v ngrok || true)"
  [[ -n "$ngrok_bin" ]] || die "ngrok missing: brew install ngrok, then  ngrok config add-authtoken <token>"
  local recorder="$REPO/recorder/.build/release/bridge43-recorder"
  if [[ ! -x "$recorder" ]]; then
    echo "… building the call recorder"
    (cd "$REPO/recorder" && swift build -c release) || die "recorder build failed"
  fi

  mkdir -p "$AGENTS" "$LOGS"
  # Stop anything started by hand on the same port / quick tunnels
  pkill -f "uvicorn app.main:app" 2>/dev/null || true
  pkill -f "bridge43-recorder" 2>/dev/null || true
  brew services stop cloudflared >/dev/null 2>&1 || true

  set_env PUBLIC_BASE_URL "https://$domain"
  if ! grep -q "^STAFF_PASSWORD=..*" "$REPO/.env"; then
    local pw; pw="$(openssl rand -base64 12 | tr -d '/+=' | cut -c1-12)"
    set_env STAFF_PASSWORD "$pw"
    echo "  Staff login for the inbox: username ps43, password $pw  (saved in .env)"
  fi

  plist com.bridge43.server "$REPO/venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8000
  plist com.bridge43.tunnel "$ngrok_bin" http "--url=https://$domain" 8000 --log stdout
  plist com.bridge43.recorder "$recorder"
  plist com.bridge43.awake /usr/bin/caffeinate -ims   # keep the Mac awake (display may still sleep)
  for l in "${LABELS[@]}"; do load "$l"; done

  sleep 6
  status
  cat <<EOF

Next, once:
  1. Twilio Console → Messaging → WhatsApp sandbox settings → "When a message comes in":
       https://$domain/webhooks/twilio/messaging   (POST)
  2. The recorder now runs in the background, so macOS asks again for its permissions. If calls stop being
     recorded: System Settings → Privacy & Security → Screen & System Audio Recording AND Microphone →
     add / enable "bridge43-recorder" ($recorder), then:  scripts/mac_services.sh restart
  3. Keep the Mac plugged in, lid open (or on an external display), WhatsApp Desktop signed in.
Inbox: http://localhost:8000  ·  public: https://$domain
EOF
}

status() {
  for l in "${LABELS[@]}"; do
    if launchctl print "gui/$UID_NUM/$l" >/dev/null 2>&1; then
      local pid; pid="$(launchctl print "gui/$UID_NUM/$l" | awk '/^\tpid =/ {print $3}')"
      if [[ -n "$pid" ]]; then ok "${l#com.bridge43.} running (pid $pid)"; else echo "! ${l#com.bridge43.} loaded but not running: see $LOGS/${l#com.bridge43.}.log"; fi
    else
      echo "- ${l#com.bridge43.} not installed"
    fi
  done
  if curl -s -m 5 http://127.0.0.1:8000/health >/dev/null; then ok "server answers on http://localhost:8000"; else echo "! server not answering yet"; fi
  local url; url="$(grep '^PUBLIC_BASE_URL=' "$REPO/.env" 2>/dev/null | cut -d= -f2-)"
  if [[ -n "$url" ]] && curl -s -m 8 "$url/health" >/dev/null; then ok "reachable from the internet at $url"; else echo "! not reachable at ${url:-?} (tunnel starting, or ngrok not signed in)"; fi
}

restart() { for l in "${LABELS[@]}"; do launchctl kickstart -k "gui/$UID_NUM/$l" 2>/dev/null || true; done; sleep 4; status; }

uninstall() {
  for l in "${LABELS[@]}"; do launchctl bootout "gui/$UID_NUM/$l" 2>/dev/null || true; rm -f "$AGENTS/$l.plist"; done
  ok "Bridge43 services removed (your data and .env are untouched)"
}

logs() { mkdir -p "$LOGS"; touch "$LOGS"/{server,tunnel,recorder}.log; tail -n 30 -f "$LOGS"/server.log "$LOGS"/recorder.log "$LOGS"/tunnel.log; }

case "${1:-}" in
  install) install "${2:-}" ;;
  status) status ;;
  restart) restart ;;
  logs) logs ;;
  uninstall) uninstall ;;
  *) sed -n '2,10p' "$0"; exit 1 ;;
esac
