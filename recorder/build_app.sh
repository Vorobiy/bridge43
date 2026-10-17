#!/bin/bash
# Build "Bridge43 Recorder.app" (menu bar app), install it in ~/Applications and start it.
#   cd ~/Downloads/bridge43/recorder && ./build_app.sh
set -euo pipefail
cd "$(dirname "$0")"

echo "… building"
swift build -c release

APP="$HOME/Applications/Bridge43 Recorder.app"
# stop older copies: the Terminal version, the background service, a previous app
launchctl bootout "gui/$(id -u)/com.bridge43.recorder" 2>/dev/null || true
pkill -f "bridge43-recorder" 2>/dev/null || true
pkill -f "Bridge43 Recorder.app/Contents/MacOS" 2>/dev/null || true
sleep 1

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp .build/release/bridge43-recorder "$APP/Contents/MacOS/Bridge43 Recorder"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Bridge43 Recorder</string>
  <key>CFBundleDisplayName</key><string>Bridge43 Recorder</string>
  <key>CFBundleIdentifier</key><string>org.ps43.bridge43.recorder</string>
  <key>CFBundleExecutable</key><string>Bridge43 Recorder</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>14.4</string>
  <key>LSUIElement</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Bridge43 records your side of WhatsApp calls with schools so they can be filed as cases.</string>
</dict>
</plist>
PLIST
# Sign with the same local certificate every time, so macOS keeps the Full Disk Access, Microphone and
# Screen Recording permissions after a rebuild (an ad-hoc signature looks like a new app each build).
CERT="Bridge43 Local Signing"
make_cert() {
  local tmp; tmp="$(mktemp -d)"
  cat > "$tmp/cert.cnf" <<CNF
[req]
distinguished_name = dn
prompt = no
x509_extensions = ext
[dn]
CN = $CERT
[ext]
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature
extendedKeyUsage = critical,codeSigning
CNF
  /usr/bin/openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -config "$tmp/cert.cnf" \
    -keyout "$tmp/key.pem" -out "$tmp/cert.pem" >/dev/null 2>&1
  /usr/bin/openssl pkcs12 -export -inkey "$tmp/key.pem" -in "$tmp/cert.pem" -name "$CERT" \
    -passout pass:bridge43 -out "$tmp/cert.p12" >/dev/null 2>&1
  security import "$tmp/cert.p12" -k "$HOME/Library/Keychains/login.keychain-db" -P bridge43 \
    -T /usr/bin/codesign >/dev/null
  echo "  macOS may ask for your Mac password once, to trust the certificate for signing."
  security add-trusted-cert -p codeSign -k "$HOME/Library/Keychains/login.keychain-db" "$tmp/cert.pem" || true
  rm -rf "$tmp"
}
sign() { codesign --force --sign "$1" --identifier org.ps43.bridge43.recorder "$APP" >/dev/null 2>&1; }
if ! security find-certificate -c "$CERT" >/dev/null 2>&1; then
  echo "… creating a local signing certificate (once)"
  make_cert || true
fi
if sign "$CERT"; then
  echo "✓ signed with \"$CERT\" (permissions survive rebuilds)"
else
  sign -
  echo "⚠️  signed ad-hoc: after this rebuild, turn Full Disk Access, Microphone and Screen Recording"
  echo "   back on for Bridge43 Recorder in System Settings → Privacy & Security."
fi
echo "✓ installed: $APP"
open "$APP"
cat <<'MSG'
✓ started: look for the phone icon at the top right of your screen.
  The first time, macOS asks to allow Screen & System Audio Recording and the Microphone for
  "Bridge43 Recorder": allow both (System Settings → Privacy & Security). Also turn it on under
  Full Disk Access (needed to read WhatsApp messages). Then click the icon → Quit and open it again
  from ~/Applications. It starts by itself at every login from now on.
MSG
