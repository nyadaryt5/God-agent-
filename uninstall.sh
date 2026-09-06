#!/usr/bin/env bash
# God-Agent uninstaller. Default: full removal. Use --keep-data to keep the state.
#   ./uninstall.sh --local --keep-data   # remove desktop app, keep config/memory/keys
#   sudo ./uninstall.sh                 # remove system service, binaries, AND state
set -euo pipefail
KEEP="0"
LOCAL="0"
for arg in "$@"; do
  case "$arg" in
    --keep-data) KEEP="1" ;;
    --local) LOCAL="1" ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done
if [[ "$(id -u)" -ne 0 || "$LOCAL" == "1" ]]; then
  DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
  rm -f "$HOME/.local/bin/goda" "$HOME/.local/bin/god-agent" "$HOME/.local/bin/god-agent-desktop"
  rm -f "$DATA_DIR/applications/god-agent.desktop" "$DATA_DIR/icons/hicolor/scalable/apps/god-agent.svg"
  rm -rf "$HOME/.god-agent/app"
  if [[ "$KEEP" == "1" ]]; then
    echo "kept $HOME/.god-agent (configuration, providers, memory, audit log)"
  else
    rm -rf "$HOME/.god-agent"
  fi
  echo "God-Agent desktop/CLI uninstalled for this user."
  exit 0
fi

systemctl stop god-agent.service 2>/dev/null || true
systemctl disable god-agent.service 2>/dev/null || true
rm -f /etc/systemd/system/god-agent.service
systemctl daemon-reload 2>/dev/null || true
rm -f /usr/local/bin/goda /usr/local/bin/god-agent
rm -rf /opt/god-agent
rm -rf /etc/god-agent

if [[ "$KEEP" == "1" ]]; then
  echo "kept /var/lib/god-agent (state, memory, audit log)"
else
  rm -rf /var/lib/god-agent
  echo "removed everything"
fi
echo "God-Agent uninstalled."
