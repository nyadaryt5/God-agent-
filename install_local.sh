#!/usr/bin/env bash
# Local (no-root) installer — runs automatically when ./install.sh is used
# without sudo, so cloning + running the installer is all that's needed.
set -euo pipefail

SRC_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
APP_DIR="$HOME/.god-agent/app"
STATE_DIR="$HOME/.god-agent"
BIN_DIR="$HOME/.local/bin"

echo "==> God-Agent local installer"
echo "    source:  $SRC_DIR"
echo "    app:     $APP_DIR"
echo "    state:   $STATE_DIR"

# 1. copy the application
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR"
cp -r "$SRC_DIR/god_agent" "$APP_DIR/"
cp "$SRC_DIR/README.md" "$APP_DIR/" 2>/dev/null || true
cp "$SRC_DIR/LICENSE" "$APP_DIR/" 2>/dev/null || true

# 2. state & config (only create config if missing — never clobber user settings)
mkdir -p "$STATE_DIR/tasks" "$STATE_DIR/evolution"
CONF="$STATE_DIR/config.json"
if [[ ! -f "$CONF" ]]; then
  python3 - "$CONF" "$STATE_DIR" <<'PY'
import json, os, sys, secrets
conf_path, state_dir = sys.argv[1:3]
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
  "api": {"enabled": True, "host": "0.0.0.0", "port": 8765,
          "token": secrets.token_urlsafe(32), "tls": False},
  "kill_switch": {"path": f"{state_dir}/DISABLED"},
  "state": {"root": state_dir, "tasks_dir": f"{state_dir}/tasks"}
}
with open(conf_path, "w") as fh:
    json.dump(cfg, fh, indent=2, sort_keys=True)
os.chmod(conf_path, 0o600)
PY
  echo "    config:  created $CONF"
else
  echo "    config:  kept existing $CONF"
fi

# 3. launchers: goda + god-agent
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/goda" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$APP_DIR\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -m god_agent.cli "\$@"
EOF
cat > "$BIN_DIR/god-agent" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$APP_DIR\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -m god_agent.cli "\$@"
EOF
chmod +x "$BIN_DIR/goda" "$BIN_DIR/god-agent"

# 4. PATH hint
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc" 2>/dev/null || true
fi

echo
echo "==> God-Agent installed. You can now run:"
echo "    goda chat      normal chat interface (type 'settings' to configure)"
echo "    goda serve     dashboard + chat UI  →  http://localhost:8765/"
echo "    goda settings  full settings menu (API providers, autonomy, sandbox…)"
echo "    goda providers add Ollama --type openai --base-url http://localhost:11434 --model llama3.1 --active"
echo
echo "    API token: $(python3 -c "import json;print(json.load(open('$CONF'))['api']['token'])")"
