#!/usr/bin/env bash
# Opens the exhibit play page full screen in Chromium once the web server answers.
# Autostarted at login by install-exhibit.sh; also usable by hand.
set -euo pipefail

PORT="${EXHIBIT_PORT:-8765}"
URL="http://127.0.0.1:$PORT/exhibit/play.html"
CHROMIUM="$(command -v chromium-browser || command -v chromium)"

# labwc and an XDG desktop can both process their respective autostart files.
# Keep only one kiosk instance even when both mechanisms fire.
mkdir -p "$HOME/.cache"
exec 9>"$HOME/.cache/acrylic-pan-kiosk.lock"
flock -n 9 || exit 0

until curl -fs "http://127.0.0.1:$PORT/api/status" >/dev/null; do
  sleep 1
done

if command -v unclutter >/dev/null; then
  # Do not let the background helper inherit fd 9; otherwise it keeps the
  # kiosk lock forever after Chromium exits and prevents a browser restart.
  unclutter -idle 1 -root 9>&- 2>/dev/null &
fi

# The profile folder is kept apart so that a crash never shows the "restore pages" bar.
rm -rf "$HOME/.config/acrylic-pan-chromium/Default/Sessions" 2>/dev/null
exec "$CHROMIUM" \
  --kiosk "$URL" \
  --user-data-dir="$HOME/.config/acrylic-pan-chromium" \
  --password-store=basic \
  --lang=ja --disable-translate --disable-features=Translate,TranslateUI \
  --noerrdialogs --disable-infobars --disable-session-crashed-bubble --no-first-run \
  --autoplay-policy=no-user-gesture-required \
  --use-fake-ui-for-media-stream \
  --overscroll-history-navigation=0 --disable-pinch \
  --check-for-update-interval=31536000
