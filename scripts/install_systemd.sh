#!/bin/sh
# Install the portfolio as systemd USER services (no root needed):
#   the dashboard, a daily database backup and a twice-daily price refresh.
#
#   scripts/install_systemd.sh             install, enable and start
#   scripts/install_systemd.sh --render D  only write the rendered unit files into directory D
#
# Stop any server you started by hand first (it holds port 8000).
set -eu

APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
UNITS="portfolio.service portfolio-backup.service portfolio-backup.timer portfolio-prices.service portfolio-prices.timer"

case "$APP_DIR" in
  *" "*|*"|"*) echo "The project path must not contain spaces or '|': $APP_DIR" >&2; exit 1 ;;
esac

render() {
  mkdir -p "$1"
  for unit in $UNITS; do
    sed "s|@APP_DIR@|$APP_DIR|g" "$APP_DIR/deploy/$unit" > "$1/$unit"
  done
}

if [ "${1:-}" = "--render" ]; then
  render "${2:?usage: --render DIR}"
  exit 0
fi

for tool in uvicorn python; do
  [ -x "$APP_DIR/.venv/bin/$tool" ] || { echo "Missing $APP_DIR/.venv/bin/$tool. Create the virtualenv first (see the README)." >&2; exit 1; }
done
if ss -ltn 2>/dev/null | grep -q ":8000 "; then
  echo "Port 8000 is already in use. Stop the server you started by hand (Ctrl+C) and run this again." >&2
  exit 1
fi

DEST="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
render "$DEST"
systemctl --user daemon-reload
systemctl --user enable --now portfolio.service portfolio-backup.timer portfolio-prices.timer
echo "Installed in $DEST and started."
if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
  echo "Services stop when you log out and do not start at boot until you run, once:"
  echo "  sudo loginctl enable-linger $USER"
fi
echo "Status:  systemctl --user status portfolio.service      Logs: journalctl --user -u portfolio.service"
echo "Timers:  systemctl --user list-timers 'portfolio-*'"
