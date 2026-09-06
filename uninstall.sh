#!/usr/bin/env bash
# God-Agent uninstaller. Default: full removal. Use --keep-data to keep the state.
#   sudo ./uninstall.sh            # remove service, binaries, AND state
#   sudo ./uninstall.sh --keep-data
set -euo pipefail
if [[ "$(id -u)" -ne 0 ]]; then
  echo "error: run as root" >&2; exit 1
fi
KEEP="0"
[[ "${1:-}" == "--keep-data" ]] && KEEP="1"

systemctl stop god-agent.service 2>/dev/null || true
systemctl disable god-agent.service 2>/dev/null || true
rm -f /etc/systemd/system/god-agent.service
systemctl daemon-reload 2>/dev/null || true
rm -rf /opt/god-agent
rm -rf /etc/god-agent

if [[ "$KEEP" == "1" ]]; then
  echo "kept /var/lib/god-agent (state, memory, audit log)"
else
  rm -rf /var/lib/god-agent
  echo "removed everything"
fi
echo "God-Agent uninstalled."
