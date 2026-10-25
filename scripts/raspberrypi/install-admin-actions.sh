#!/usr/bin/env bash
# Installs only the fixed, root-owned actions used by the localhost admin UI.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
USER_NAME="$(id -un)"
SOURCE="$REPO/scripts/raspberrypi/system-action-helper.sh"
SUDOERS_TMP="$(mktemp)"
trap 'rm -f "$SUDOERS_TMP"' EXIT

for name in service-restart reboot poweroff; do
  sudo install -o root -g root -m 0755 "$SOURCE" "/usr/local/sbin/acrylic-pan-$name"
done

cat >"$SUDOERS_TMP" <<EOF
$USER_NAME ALL=(root) NOPASSWD: /usr/local/sbin/acrylic-pan-service-restart
$USER_NAME ALL=(root) NOPASSWD: /usr/local/sbin/acrylic-pan-reboot
$USER_NAME ALL=(root) NOPASSWD: /usr/local/sbin/acrylic-pan-poweroff
EOF
sudo visudo -cf "$SUDOERS_TMP"
sudo install -o root -g root -m 0440 "$SUDOERS_TMP" /etc/sudoers.d/acrylic-pan-exhibit
echo "Admin system actions installed for $USER_NAME"
