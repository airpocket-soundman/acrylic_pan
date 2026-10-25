#!/usr/bin/env bash
# Installs remote-maintenance tools for the Raspberry Pi exhibit host.
# Firmware build/flash tools stay on the Windows LEXIDE machine.
set -euo pipefail

USER_NAME="$(id -un)"

echo "== Raspberry Pi remote-maintenance packages"
sudo apt-get update
sudo apt-get install -y curl git nodejs npm

echo "== Codex CLI"
sudo npm install -g @openai/codex@latest
codex --version

echo "== Tailscale"
if ! command -v tailscale >/dev/null 2>&1; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi
sudo systemctl enable --now tailscaled
# Let the desktop user complete login and inspect status without repeated sudo.
sudo tailscale set --operator="$USER_NAME"
tailscale version

cat <<EOF

Remote-maintenance tools installed. The official Solist-AI firmware build,
flash, LEXIDE debugger, and Solist-AI Simulator remain on Windows x64.

Tailscale still needs account authorization. Run:
  tailscale up --hostname=Solistpan

Codex still needs OpenAI account authorization. Run:
  codex login
EOF
