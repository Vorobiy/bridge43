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
codesign --force --sign - --identifier org.ps43.bridge43.recorder "$APP" >/dev/null
echo "✓ installed: $APP"
open "$APP"
cat <<'MSG'
✓ started: look for the phone icon at the top right of your screen.
  The first time, macOS asks to allow Screen & System Audio Recording and the Microphone for
  "Bridge43 Recorder": allow both (System Settings → Privacy & Security), then click the icon → Quit
  and open it again from ~/Applications. It starts by itself at every login from now on.
MSG
