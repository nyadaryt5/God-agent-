# Native desktop app

God-Agent's desktop interface is a real Tk window, **not a browser or a webview**.
It calls `Runtime` and `Agent` in the same Python process. It does not start an
HTTP server, bind a localhost port, connect to `goda serve`, or require a
dashboard API token. The old browser dashboard remains an explicit opt-in.

## Install on the machine you want to manage

Requirements: **Linux**, Python **3.10+**, Python's Tk package, and an active
graphical desktop (X11 or a Wayland session with XWayland). The existing system
tools are Linux-specific; native Windows and macOS support is not implemented.

From the repository on your Linux computer:

```bash
# Ubuntu / Debian — install this system dependency once:
sudo apt install python3-tk

# Install the app for your normal desktop user, WITHOUT sudo:
./install.sh --desktop
export PATH="$HOME/.local/bin:$PATH"
goda desktop
```

For Fedora use `sudo dnf install python3-tkinter`; for Arch use
`sudo pacman -S tk`. Tk is an OS/Python component, not a pip dependency.

You can also launch **God-Agent** from your application menu, run
`god-agent-desktop`, or use `python3 -m god_agent.desktop` directly from the
checkout. A Python package installation (`pip install .`) supplies the
`god-agent-desktop` entry point too; the shell installer additionally supplies
the Linux menu entry and icon.

`goda` and `god-agent` without arguments launch the desktop when a display is
available, and show CLI help on headless systems. Explicit CLI commands retain
their existing behavior. Use `goda chat` for an SSH/headless terminal.

### What is installed

| Location | Purpose |
|---|---|
| `~/.god-agent/app/` | Application code |
| `~/.local/bin/{goda,god-agent,god-agent-desktop}` | Launchers |
| `${XDG_DATA_HOME:-~/.local/share}/applications/god-agent.desktop` | Application-menu entry |
| `${XDG_DATA_HOME:-~/.local/share}/icons/hicolor/scalable/apps/god-agent.svg` | App icon |
| `~/.god-agent/config.json` | Configuration |
| `~/.god-agent/providers.json` | Provider profiles and optional saved keys |
| `~/.god-agent/` | Chat history, memory, Brain, audit log, and other state |

Installation does **not** start the app, create an autostart entry, elevate the
app to root, or install/start a system service. Local launchers select the user
configuration, so an unrelated `/etc/god-agent/config.json` cannot redirect the
desktop into a system service's state. `GODA_CONFIG` can override that explicitly.

Upgrades preserve your existing config, selected provider, credentials, and
state. Uninstall only the app and keep data with:

```bash
./uninstall.sh --local --keep-data
```

Omit `--keep-data` only if you also want to remove your configuration, saved
credentials, memory, and audit data.

### Already installed the system service?

The desktop does not use that service and does not stop it automatically. If
you no longer want the old service/dashboard running, explicitly stop it:

```bash
sudo systemctl disable --now god-agent.service
```

This also disables the service's background watchdog. You can keep the service
instead, but its HTTP listener is separate from the desktop app. Do not use
`sudo ./install.sh` for a desktop-only installation.

## Kira is the default AI provider

Fresh installations preselect:

- **Provider:** Kira
- **API format:** `openai` (OpenAI-compatible)
- **Base URL:** `https://kiraai.vn/api/v1`
- **Initial model:** `kira-3.5-flash`
- **Credential environment variable:** `KIRA_API_KEY`

In **AI provider**, enter your own API key and click **Save & use**. Existing
installations keep their active provider; choose **Kira preset** and save to
switch explicitly. You can also add custom providers by editing the name,
format, URL, and model.

The app and its tools execute **locally**, but Kira is a **remote service**.
Prompts, task context, and tool results used by the agent can be sent to the
selected provider. On-device model inference is a separate setup, not implied
by installing a desktop interface.

### Credentials

- No API key is included in source code, desktop entries, or installers.
- The key input is masked. A blank input keeps an existing saved key when the
  endpoint and API format are unchanged. Use **Remove saved key** to clear it.
- Saved provider keys are stored locally with owner-only `0600` permissions,
  including the temporary file used during atomic writes. They are **not
  encrypted** and remain accessible to your account and root.
- Alternatively leave the saved key empty and set `KIRA_API_KEY` in the
  environment that launches the app. These environment credentials are
  resolved at request time rather than written into the provider file.
- Environment variables set in a terminal do not automatically reach apps
  launched by an already-running desktop menu. Save the key in the app or
  launch `goda desktop` from that terminal.
- Do not paste keys into chat/tasks or commit them in files. Rotate a key if it
  has been shared. Kira-format tokens and common other secrets are redacted
  from displayed task output on a best-effort basis.

`GODA_API_KEY`, `GODA_MODEL`, and `GODA_BASE_URL` remain explicit runtime
overrides. Remove stale overrides if a saved provider appears not to take
effect. A provider's own key environment variable is replaced when switching
profiles, rather than accidentally reusing Kira's key on another host.

### Models and connection tests

The **Model ID** field is editable. **Test connection / load models** probes
the form without saving changes and populates the dropdown when `/models` is
available. Choose a model, then click **Save & use**. The model list may be
public: a successful list response does **not** validate the key, account
balance, or access to a particular model. If the provider has no model-list
endpoint, testing can send a minimal one-token chat request.

Without a configured key the app clearly labels its **offline diagnostic
planner**. It can run basic built-in checks, but this is not a live AI model.
No provider request is made just by opening the window.

## Using the app

- **Chat:** send a task with Enter (Shift+Enter inserts a newline). System
  status and disk-usage shortcuts run read-only diagnostic tasks.
- **Task output:** shows worker progress and each tool's output.
- **Approvals:** policy-gated actions display a scrollable native dialog,
  defaulting to denial. No browser-based approval broker is involved.
- **Disable agent:** activates the existing persistent kill switch and denies
  a pending approval. **It blocks subsequent steps, but does not terminate an
  already-running command or AI request.** Enable it again explicitly to resume.
- **Closing:** the app asks you to wait if an operation is still running, rather
  than abandoning a native command or closing its database underneath it.

The app runs with the current account's permissions; it does not silently
request root or change your policy. Current autonomy, approval, sandbox, and
privilege settings are visible in the footer. To change the broader policy,
close the app and use the existing `goda settings` CLI, then reopen it. For
example, to require approval for high-risk actions:

```bash
goda settings policy.autonomy supervised --save
goda settings policy.approval ask --save
```

All existing policy checks, the Brain's two-writer rule, and auditing stay in
the core runtime. The UI does not bypass them.

## Developer notes / tests

`desktop.py` contains the headlessly testable controller. It serializes work,
binds the runtime context in each worker thread, and sends results through a
queue. `desktop_ui.py` is the Tk view; only the main thread touches widgets.
Tk imports are lazy, so CLI/server operation does not require GUI packages.

```bash
make test                  # includes stdlib desktop/provider/installer tests
python3 -m pytest -q        # full suite; the graphical test skips without Tk/display
# With Tk, Xvfb, and xauth installed on a Linux test machine:
xvfb-run -a python3 -m pytest -q
```

Automated HTTP tests mock the provider; they do not spend API credit or use real
credentials. Installer tests run in a disposable HOME and assert that no
system service is started. The graphical test requires a real or virtual
display; a hosted browser preview is not a native desktop session.
