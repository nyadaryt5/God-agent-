#!/usr/bin/env bash
# God-Agent installer.
#
#   ./install.sh              # automatic: local (no sudo) OR system-wide if root
#   sudo ./install.sh         # system-wide: /opt/god-agent + systemd service (root-capable)
#   ./install.sh --local      # force local install to ~/.god-agent (no root needed)
#   ./install.sh --user goda  # system-wide as a dedicated user
#   ./install.sh --no-start   # system-wide, don't start the service
#
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="root"
START="1"
FORCE_LOCAL="0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) RUN_USER="$2"; shift 2 ;;
    --no-start) START="0"; shift ;;
    --local) FORCE_LOCAL="1"; shift ;;
    --root) RUN_USER="root"; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 >= 3.10 is required" >&2
  exit 1
fi

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1 || {
  echo "error: python3 >= 3.10 is required (found $(python3 --version 2>&1))" >&2
  echo "       please install or switch to python3.10+ (e.g. sudo apt install python3.11)" >&2
  exit 1
}

if [[ "$(id -u)" -ne 0 ]] || [[ "$FORCE_LOCAL" == "1" ]]; then
  exec bash "$SRC_DIR/install_local.sh" "$SRC_DIR"
fi

# ---------------------------------------------------------------- system install
echo "==> God-Agent system installer"
echo "    source:   $SRC_DIR"
echo "    install:  /opt/god-agent"
echo "    run as:   $RUN_USER"

PREFIX="/opt/god-agent"
CONF_DIR="/etc/god-agent"
STATE_DIR="/var/lib/god-agent"
BIN_DEST="/usr/local/bin"

rm -rf "$PREFIX"
mkdir -p "$PREFIX"
cp -r "$SRC_DIR/god_agent" "$PREFIX/"
cp "$SRC_DIR/README.md" "$PREFIX/" 2>/dev/null || true
cp "$SRC_DIR/LICENSE" "$PREFIX/" 2>/dev/null || true
if [[ -d "$SRC_DIR/tests" ]]; then cp -r "$SRC_DIR/tests" "$PREFIX/" 2>/dev/null || true; fi
cp "${BASH_SOURCE[0]}" "$PREFIX/install.sh"
cp "$SRC_DIR/install_local.sh" "$PREFIX/install_local.sh" 2>/dev/null || true

mkdir -p "$CONF_DIR" "$STATE_DIR" "$STATE_DIR/evolution" "$STATE_DIR/state" "$STATE_DIR/tasks"
TOKEN_FILE="$CONF_DIR/token"
if [[ ! -f "$TOKEN_FILE" ]]; then
  umask 077
  python3 -c "import secrets; print(secrets.token_urlsafe(32))" > "$TOKEN_FILE"
fi
TOKEN="$(cat "$TOKEN_FILE")"

python3 - "$CONF_DIR/config.json" "$STATE_DIR" "$TOKEN" <<'PY'
import json, sys
conf_path, state_dir, token = sys.argv[1:4]
cfg = {
  "agent": {"name": "God-Agent", "max_steps": 24, "model": "gpt-4o-mini",
            "temperature": 0.2, "task_timeout_s": 0, "watchdog_interval_s": 60},
  "llm": {"provider": "openai", "api_key_env": "GODA_API_KEY", "api_key": "",
          "base_url": "", "model": "gpt-4o-mini", "timeout_s": 120, "max_retries": 2},
  "execution": {"memory_limit_mb": -1, "cpu_limit_s": -1, "max_processes": -1,
                "kill_switch_interval_s": 2},
  "policy": {"autonomy": "autonomous", "approval": "auto", "sandbox": "none",
             "shell": {"enabled": True, "allow_network": True, "max_output_chars": 500000},
             "files": {"enabled": True, "protected": [], "max_read_chars": 2000000},
             "network": {"enabled": True, "max_bytes": 8000000, "timeout_s": 60},
             "evolution": {"enabled": True, "auto_apply": False, "max_attempts_per_day": 10}},
  "memory": {"db_path": f"{state_dir}/memory.db", "max_episodes": 5000, "max_reflections": 10000},
  "brain": {"path": f"{state_dir}/brain.json", "max_entries": 5000, "encryption": "auto",
            "inject_credentials": False, "inject_prompts": True, "auto_write": True},
  "self_model": {"path": f"{state_dir}/self.json", "max_update_chars": 2000, "max_versions": 100},
  "audit": {"path": f"{state_dir}/audit.jsonl", "max_mb": 256},
  "api": {"enabled": True, "host": "0.0.0.0", "port": 8765, "token": token, "tls": False},
  "kill_switch": {"path": f"{state_dir}/DISABLED"},
  "state": {"root": state_dir, "tasks_dir": f"{state_dir}/tasks"}
}
with open(conf_path, "w") as fh:
    json.dump(cfg, fh, indent=2, sort_keys=True)
PY
chmod 600 "$CONF_DIR/config.json" "$TOKEN_FILE"

if [[ "$RUN_USER" != "root" ]]; then
  id -u "$RUN_USER" >/dev/null 2>&1 || useradd --system --home "$STATE_DIR" --shell /usr/sbin/nologin "$RUN_USER"
  chown -R "$RUN_USER" "$STATE_DIR"
fi

# ------------------------------------------------------------- launchers on PATH
mkdir -p "$BIN_DEST"
cat > "$BIN_DEST/goda" <<'EOF'
#!/usr/bin/env bash
export PYTHONPATH="/opt/god-agent${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m god_agent.cli "$@"
EOF
cat > "$BIN_DEST/god-agent" <<'EOF'
#!/usr/bin/env bash
export PYTHONPATH="/opt/god-agent${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m god_agent.cli "$@"
EOF
chmod 755 "$BIN_DEST/goda" "$BIN_DEST/god-agent"

# ------------------------------------------------------------- systemd service
mkdir -p /etc/systemd/system
cat > /etc/systemd/system/god-agent.service <<UNITEOF
[Unit]
Description=God-Agent — self-aware AI system administrator
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
ExecStart=$PREFIX/god_agent/run_daemon.py
Restart=always
RestartSec=5
Environment=GODA_CONFIG=$CONF_DIR/config.json
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$STATE_DIR
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
UNITEOF
chmod 644 /etc/systemd/system/god-agent.service 2>/dev/null || true

systemctl daemon-reload >/dev/null 2>&1 || true
systemctl enable god-agent.service >/dev/null 2>&1 || true
if [[ "$START" == "1" ]]; then
  systemctl restart god-agent.service >/dev/null 2>&1 || true
  sleep 1
  systemctl --no-pager status god-agent.service 2>/dev/null | head -6 || true
fi

echo
echo "==> God-Agent installed system-wide."
echo "    commands: goda, god-agent (installed in $BIN_DEST)"
echo "    service:  systemctl status god-agent"
echo "    config:   $CONF_DIR/config.json"
echo "    API:      http://localhost:8765/   (token in $CONF_DIR/token)"
echo "    chat:     goda chat   |   settings: goda settings | providers: goda providers"
