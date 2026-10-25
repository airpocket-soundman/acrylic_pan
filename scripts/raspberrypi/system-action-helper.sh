#!/usr/bin/env bash
# Installed root-owned under one of three fixed names. No arguments are accepted.
set -euo pipefail

if [ "$#" -ne 0 ]; then
  echo "arguments are not accepted" >&2
  exit 2
fi

case "$(basename "$0")" in
  acrylic-pan-service-restart)
    exec /usr/bin/systemctl restart acrylic-pan-exhibit.service
    ;;
  acrylic-pan-reboot)
    exec /usr/bin/systemctl reboot
    ;;
  acrylic-pan-poweroff)
    exec /usr/bin/systemctl poweroff
    ;;
  *)
    echo "invalid helper name" >&2
    exit 2
    ;;
esac
