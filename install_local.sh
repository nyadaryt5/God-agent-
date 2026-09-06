#!/usr/bin/env bash
# User install — CLI + native desktop launcher, without a system service.
# Called by install.sh; --desktop additionally requires Python Tk up front.
set -euo pipefail

SRC_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
APP_DIR="$HOME/.god-agent/app"
STATE_DIR="$HOME/.god-agent"
BIN_DIR="$HOME/.local/bin"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
DESKTOP_MODE="${2:-}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 >= 3.10 is required" >&2
  exit 1
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1 || {
  echo "error: python3 >= 3.10 is required (found $(python3 --version 2>&1))" >&2
  exit 1
}
if [[ "$DESKTOP_MODE" == "--desktop" && "$(uname -s)" != "Linux" ]]; then
  echo "error: native system control currently supports Linux desktops only" >&2
  exit 1
fi
HAS_TK="1"
if ! python3 -c 'import tkinter' >/dev/null 2>&1; then
  HAS_TK="0"
  if [[ "$DESKTOP_MODE" == "--desktop" ]]; then
    echo "error: the desktop app requires Python Tk (no pip package needed)." >&2
    echo "  Ubuntu/Debian: sudo apt install python3-tk" >&2
    echo "  Fedora: sudo dnf install python3-tkinter   |   Arch: sudo pacman -S tk" >&2
    echo "Then run ./install.sh --desktop again, without sudo." >&2
    exit 1
  fi
fi

echo "==> God-Agent local installer"
echo "    source:  $SRC_DIR"
echo "    app:     $APP_DIR"
echo "    state:   $STATE_DIR"

# 1. copy the application (data lives outside app/ and is preserved on upgrades)
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR"
cp -r "$SRC_DIR/god_agent" "$APP_DIR/"
cp "$SRC_DIR/README.md" "$APP_DIR/" 2>/dev/null || true
cp "$SRC_DIR/LICENSE" "$APP_DIR/" 2>/dev/null || true

# 2. create config only if missing; use the same defaults as the Python package
mkdir -p "$STATE_DIR/tasks" "$STATE_DIR/evolution"
CONF="$STATE_DIR/config.json"
if [[ ! -f "$CONF" ]]; then
  python3 - "$APP_DIR" "$CONF" "$STATE_DIR" <<'PY'
import json, os, secrets, sys
app_dir, conf_path, state_dir = sys.argv[1:4]
sys.path.insert(0, app_dir)
from god_agent.config import default_config
cfg = default_config()
cfg["state"] = {"root": state_dir, "tasks_dir": f"{state_dir}/tasks"}
for section, key, filename in (
    ("memory", "db_path", "memory.db"), ("brain", "path", "brain.json"),
    ("self_model", "path", "self.json"), ("audit", "path", "audit.jsonl"),
    ("kill_switch", "path", "DISABLED"),
):
    cfg[section][key] = os.path.join(state_dir, filename)
cfg["api"]["enabled"] = False
cfg["api"]["token"] = secrets.token_urlsafe(32)  # only used if the operator runs `goda serve`
cfg["agent"]["watchdog_interval_s"] = 60
fd = os.open(conf_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as fh:
    json.dump(cfg, fh, indent=2, sort_keys=True)
PY
  echo "    config:  created $CONF"
else
  echo "    config:  kept existing $CONF"
fi

# 3. launchers and native application-menu entry; no HTTP service is started
mkdir -p "$BIN_DIR" "$DATA_DIR/applications" "$DATA_DIR/icons/hicolor/scalable/apps"
python3 - "$APP_DIR" "$STATE_DIR" "$BIN_DIR" "$DATA_DIR" <<'PY'
import shlex, shutil, sys
from pathlib import Path
app, state, bin_dir, data = map(Path, sys.argv[1:5])
for name, module in (("goda", "god_agent.cli"), ("god-agent", "god_agent.cli"),
                     ("god-agent-desktop", "god_agent.desktop")):
    script = "#!/usr/bin/env bash\n"
    script += "APP_DIR=" + shlex.quote(str(app)) + "\n"
    script += "DEFAULT_CONFIG=" + shlex.quote(str(state / "config.json")) + "\n"
    script += 'export PYTHONPATH="$APP_DIR${PYTHONPATH:+:$PYTHONPATH}"\n'
    script += 'export GODA_CONFIG="${GODA_CONFIG:-$DEFAULT_CONFIG}"\n'
    script += f'exec python3 -m {module} "$@"\n'
    launcher = bin_dir / name
    launcher.write_text(script, encoding="utf-8")
    launcher.chmod(0o755)

# Desktop Entry Exec has two layers of escaping, plus % field-code expansion.
def exec_arg(value):
    value = value.replace("%", "%%")
    for char in ('\\', '"', '`', '$'):
        value = value.replace(char, "\\" + char)
    return '"' + value.replace("\\", "\\\\") + '"'

entry = "[Desktop Entry]\nVersion=1.0\nType=Application\nName=God-Agent\n"
entry += "Comment=Native AI system assistant — no browser required\n"
entry += "Exec=" + exec_arg(str(bin_dir / "god-agent-desktop")) + "\n"
entry += "Icon=god-agent\nTerminal=false\nCategories=System;Utility;\n"
entry += "StartupNotify=true\nStartupWMClass=GodAgent\n"
entry += "Keywords=AI;assistant;system;chat;\n"
desktop_file = data / "applications/god-agent.desktop"
desktop_file.write_text(entry, encoding="utf-8")
desktop_file.chmod(0o644)
shutil.copyfile(app / "god_agent/static/god-agent.svg",
                data / "icons/hicolor/scalable/apps/god-agent.svg")
PY
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$DATA_DIR/applications" >/dev/null 2>&1 || true
fi

# 4. PATH configuration (bash, zsh, profile)
PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
  if [[ -f "$rc" || "$rc" == "$HOME/.bashrc" ]]; then
    if ! grep -qsF "$PATH_LINE" "$rc" 2>/dev/null; then
      echo "$PATH_LINE" >> "$rc" 2>/dev/null || true
    fi
  fi
done

echo
echo "==> God-Agent installed as a native application for your user."
echo "    Open God-Agent from your application menu, or run:"
echo "    goda desktop   standalone desktop window (no browser / local HTTP server)"
echo "    goda chat      terminal chat (type 'settings' to configure)"
echo "    goda settings  command-line settings menu"
echo "    goda serve     OPTIONAL browser dashboard (not started by this install)"
echo "    AI default:    Kira — https://kiraai.vn/api/v1 — kira-3.5-flash"
echo "    API key:       enter it in the desktop AI provider tab, or set KIRA_API_KEY"
echo "    data:          $STATE_DIR (existing config/providers are preserved)"
if [[ "$HAS_TK" == "0" ]]; then
  echo "    Desktop needs Python Tk: sudo apt install python3-tk (Ubuntu/Debian)."
  echo "    Fedora: sudo dnf install python3-tkinter | Arch: sudo pacman -S tk"
fi
echo
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  echo "NOTE: $BIN_DIR is not in your current PATH."
  echo "      Run this command now, or open a new terminal:"
  echo '      export PATH="$HOME/.local/bin:$PATH"'
fi
