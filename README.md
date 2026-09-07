# ☩ God-Agent (`goda`)

> A self-aware, self-evolving AI system administrator for Linux servers —
> available as a **native desktop app**, CLI, or system service with optional root capability. It runs
> **natively, unsandboxed** with no artificial resource caps, has a **Brain**
> (credentials + important prompts + learnings, two-writer rule), an honest
> self-model, a guarded evolution loop, a full audit trail, and a dashboard.

⚠️ **Read this first:** God-Agent is not sentient and this project does not
claim it is. It has an *operational self-model* and a *guarded self-improvement
loop*, which is the honest, engineering version of "consciousness". See
[docs/CONSCIOUSNESS.md](docs/CONSCIOUSNESS.md).

---

## What it does

| Capability | Description |
|---|---|
| **Developer mode** | Operator-only toggle (`goda dev on` / ⚡ DEV button / API). When ON the agent **never refuses** a command — approval gate, risk blocks, and catastrophic-command blocks are lifted (everything still audited). The AI cannot enable it itself. |
| **Full native system control** | Uses every bit of RAM, all CPU cores, and all GPUs — no sandbox, no artificial memory/CPU/process caps (`execution.* = -1`). Shell, files, services, packages, processes (kill/nice/affinity), sysctl, cron, CPU governor/IO scheduler tuning, hardware & GPU inventory. |
| **The Brain** | Durable store for credentials (AES-256 encrypted), standing prompts, facts, and learnings. **Two writers only:** your command is ABSOLUTE; the AI auto-writes only what it judges important. Enforced in code — the AI can never touch your entries. See [docs/BRAIN.md](docs/BRAIN.md). |
| **Self-evolution** | The agent proposes changes to itself (new tools, heuristics, docs). Every proposal is validated, built in an isolated tree, byte-compiled, tested, and then **applied (reversible git commit) or handed to a human**. It can never evolve its Constitution or policy code. |
| **Self-model ("self-awareness")** | Persistent model of its identity, capabilities, boundaries, stats, and lessons. It can introspect itself and, within strict limits, update its own self-description. |
| **Memory** | Every task becomes an episode; every task ends with a reflection; TF-IDF recall pulls relevant history into context. |
| **Real browser automation** | Ten `browser_*` tools drive a genuine headless Chromium — **JavaScript runs, cookies persist, sessions survive across steps**, so logins and SPAs work. `open` · `click` · `type` · `extract` · `links` · `wait` · `screenshot` · `eval` · `stealth_check` · `close`. Anti-bot-detection hardening on by default. Policy-graded and audited like every other tool (unlike an MCP browser server, which cannot be risk-graded). See [Browser automation](#browser-automation). |
| **Real agent engine** | Runs the **OpenAI Agents SDK** (MIT, from OpenAI) when an OpenAI-compatible endpoint + key is configured — genuine turn-by-turn reasoning and native function-calling through God-Agent's own tools. Falls back to a built-in offline heuristic planner when no model is available. Pluggable: CrewAI, LangGraph, **Hermes Agent** (Nous Research), **UI-TARS** (ByteDance), **Grok Build** (xAI), smolagents, and AutoGen adapters activate when installed. |
| **One body, not a crew** | God-Agent is **one organism**. The 100-specialist roster is compressed into **6 functional parts** — 1 **Brain** (reason/memory), 2 **Hands** (Left=act, Right=build), 2 **Legs** (Left=reach/network, Right=move/data), 1 **Torso** (core/guard/cloud). Each part is a real agent that folds its specialists' tools, so nothing is lost. `goda catalog` shows the body, `goda catalog --roster` lists the full 100-specialist portfolio. Add your own agents via `~/.god-agent/agents.json` or `~/.god-agent/agents.d/*.json`. Every tool call from *any* part is still policy-checked and audited. Toggle with `agent.swarm`, pick the engine with `agent.engine`. |
| **Guardrails (Constitution, not capability limits)** | Immutable Constitution (C1–C7). The only things it cannot do are ones no operator-sane root agent should: no backdooring humans out, no exfiltrating secrets, no hiding actions, never `rm -rf /`-style host destruction. Everything else is native and unlimited. |
| **Trust** | Append-only, **hash-chained audit log** — tampering is detectable with `goda audit --verify`. Kill switch (`goda disable`) whenever you want it to stop. |
| **Interfaces** | **Native desktop app** (`goda desktop`, no browser/server), CLI (`goda run/chat/brain/status...`), and an optional HTTP API/dashboard. |
| **Web search** | `web_search` returns titles, URLs, and snippets. Works **out of the box with no API key** (DuckDuckGo), or point it at Brave, Tavily, or a self-hosted SearXNG. Turns "why does nginx throw 502" into an answer instead of a guessed URL. |
| **Vision** | `image_analyze` actually *looks* at an image. This is what makes `browser_screenshot` useful: capture a page, then read it. Also reads diagrams, charts, and error dialogs. |
| **Image generation** | `image_generate` produces diagrams and graphics from a prompt — architecture diagrams, network topologies. |
| **Speech** | `speak` turns text into an audio file. A monitoring agent that says "disk critical on db-01" beats one that writes another log line; set `media.play_audio` to hear it. |
| **Documents with images** | Eleven `doc_*` tools build a real document — headings, text, code, quotes, **embedded images**, page breaks — stored as an editable block model, rendering to **md / html / pdf / docx**. Edit block 3 without regenerating the rest. See [Documents](#documents-with-images). |
| **Image editing** | `image_info` / `image_edit` / `image_compose` — resize, crop, rotate, convert, and build before/after grids, so screenshots are the right size before they go in a report. |
| **LLM-agnostic** | **Kira is the default** (`https://kiraai.vn/api/v1`, model `kira-3.5-flash`). Also supports OpenAI-compatible (OpenAI, OpenRouter, Ollama, vLLM, LM Studio...), Anthropic, or offline **mock mode** with a built-in heuristic planner so it's usable with zero API keys. |

## Step-by-step setup on Linux

**1. Clone the repository**

```bash
git clone https://github.com/nyadaryt5/God-agent-.git
cd God-agent-
```

**2. Install the native desktop app (no browser or localhost server)**

On Ubuntu/Debian, install Python's Tk support once:

```bash
sudo apt install python3-tk
./install.sh --desktop        # run WITHOUT sudo, as your desktop user
```

Fedora: `sudo dnf install python3-tkinter`. Arch: `sudo pacman -S tk`.
Python 3.10+ and a Linux graphical session are required. Native Windows/macOS
system control is not currently supported.

The app installs to `~/.god-agent/app`, adds **God-Agent** to your application
menu, and installs the `goda`, `god-agent`, and `god-agent-desktop` commands.
It does **not** install or start a web server, system service, or autostart task.

Other installation modes remain available:

- `./install.sh --local`: user install, also works without Tk for terminal use.
- `sudo ./install.sh`: system-wide service in `/opt/god-agent`, with root
  capability and the HTTP dashboard. **This is not needed for the desktop app.**
- Existing settings, keys, and selected providers are preserved on upgrades.

**3. Open God-Agent**

Launch **God-Agent** from your application menu, or run:

```bash
goda desktop                 # standalone native window
god-agent-desktop            # same app
# or, without installing:
python3 -m god_agent.desktop
```

The desktop has chat, task output, operator approval dialogs, an enable/disable
control, and an **AI provider** tab. It calls the agent directly in the same
process, with your account's permissions. No HTTP connection or API token is
needed between the window and the agent. `goda`/`god-agent` without arguments
also opens the desktop when a graphical display is available; on a headless
machine it prints CLI help.

`localhost:8765` is only the address of the **optional browser dashboard**;
it does not determine where system commands execute. To use other interfaces:

```bash
goda chat                    # terminal-only chat
goda serve                   # OPTIONAL browser dashboard, explicitly opt in
```

See [docs/DESKTOP.md](docs/DESKTOP.md) for installation, credentials, and
troubleshooting. An app launched in a hosted development environment controls
that environment, **not your personal computer**. Install it on the machine
you want to manage.

**4. Configure it — type `settings`**

```bash
goda settings      # interactive menu (or type "settings" in chat)
```

In the native app, open **AI provider** (or type `settings` in chat) to set
up the endpoint, key, and model. The CLI settings menu additionally controls
autonomy, approval mode, sandbox, network, evolution, Brain behavior, resource
caps, and the optional API port.

**5. Configure Kira (the default), or add another provider**

For a fresh install, **Kira** is already selected:

- Base URL: `https://kiraai.vn/api/v1`
- API format: OpenAI-compatible (`openai`)
- Initial model: `kira-3.5-flash` (editable)
- Credential environment variable: `KIRA_API_KEY`

In the desktop **AI provider** tab, enter your own API key and click
**Save & use**. No key is bundled in the source or installer. Saved keys are
kept in your local `~/.god-agent/providers.json` with owner-only permissions
(`0600`); they are **not encrypted**. To avoid saving a key to disk, set
`KIRA_API_KEY` in the environment that launches the app instead. Rotate any
key that has been shared publicly or pasted into a chat.

**Test connection / load models** can populate the model selector. A public
`/models` response verifies reachability, not key validity or account credit.
AI requests go to Kira's remote service even though the app and system tools
run locally. Without a key, the offline diagnostic planner remains available.

Upgrading does not replace an existing active provider. Click **Kira preset**,
enter your Kira key, then **Save & use** to switch. CLI equivalent (using an
environment variable, not a key in your shell history):

```bash
goda providers add Kira --type openai --base-url https://kiraai.vn/api/v1 \
    --model kira-3.5-flash --key-env KIRA_API_KEY --active
```

Other providers are still supported:

```bash
goda providers add "Ollama" --type openai \
    --base-url http://localhost:11434 --model llama3.1 --active

goda providers add "My Server" --type openai \
    --base-url https://my-llm.example.com/v1 --key sk-xxx --model my-model --active

goda providers add "OpenRouter" --type openai \
    --base-url https://openrouter.ai/api/v1 --key sk-or-xxx --model anthropic/claude-3.5-sonnet --active

goda providers test "Ollama"     # verify connection
goda providers list              # see all, switch with `goda providers use NAME`
```

Any **OpenAI-compatible** endpoint works (OpenAI, Ollama, vLLM, LM Studio,
LocalAI, OpenRouter, Groq, Together…), plus Anthropic, plus offline/mock mode
(no API key at all). The UI dashboard settings panel does all of this too:
click **⚙ Settings** (or type `settings` in the web chat).

**6. Start using it**

```bash
goda chat
> make sure nginx is healthy and disk is clean
goda run "summarize recent errors"
goda status
goda brain set db_pass 'secret' --kind credential --lock
```

**7. Developer mode — stop refusals (operator-only)**

```bash
goda dev on     # the agent will not refuse any command (still audited)
goda dev off    # normal guardrails back
# in chat:  dev on / dev off / dev
# dashboard: ⚡ DEV button, or ⚙ Settings → Developer mode
```

The AI can never enable developer mode itself — `developer_mode` is an
operator-only Brain key. Details: [docs/DEVELOPER_MODE.md](docs/DEVELOPER_MODE.md).

The native app needs no HTTP API token. If you explicitly installed the
system-wide service, its separate dashboard API can be accessed with:

```bash
curl -H "Authorization: Bearer $(sudo cat /etc/god-agent/token)" \
    http://localhost:8765/api/status
```

`GODA_BASE_URL`, `GODA_MODEL`, and `GODA_API_KEY` remain explicit overrides for
the selected provider. Only use a key issued by the endpoint you are calling.
For a model running on your machine, configure an Ollama/local provider; Kira
is a remote API, not an on-device model.

When the HTTP server is explicitly running, open `http://<server>:8765/` for
the **web chat + dashboard** (type
`settings` in the chat or click ⚙ Settings to configure), or use the CLI:

```bash
goda chat                     # chat (type 'settings' for settings, 'providers' for providers)
goda run "check disk usage and tell me what to clean"
goda settings                 # full settings menu
goda providers list           # API providers (custom supported)
goda status
goda memory search "nginx"
goda audit --verify
```

## Development / offline demo

```bash
pip install -e '.[agents]'             # optionally install the real agent engine
pip install -e '.[browser]'            # optionally install headless browser automation
python3 -m god_agent.selftest          # 0-dependency core tests
python3 -m pytest -q                   # full test suite (or: make test)
python3 -m god_agent.desktop           # native app (requires Tk + desktop display)
goda run "status"                      # works with the built-in heuristic planner
goda serve                             # OPTIONAL dashboard, no LLM key needed
```

When you configure an OpenAI-compatible endpoint with a key (`goda providers
use` / the AI provider tab), God-Agent switches to the **real agent engine**
(OpenAI Agents SDK) and, by default, runs a **multi-agent swarm** — a God
orchestrator delegating to a data-driven crew of specialists. To use a single
reasoning agent instead, set `agent.swarm` to `false`
(`goda settings agent.swarm false`).

**Grow the crew ("every agent").** Add any number of agents by writing
definitions to `~/.god-agent/agents.json` or dropping `*.json` files into
`~/.god-agent/agents.d/` (see [`config/agents.example.json`](config/agents.example.json)
for the format — `name`, `handoff`, `instructions`, optional `tools` filter, and
`engine`). The God orchestrator auto-discovers them. Inspect the active crew:

```bash
goda catalog                     # list all specialist agents + engines
goda catalog --domains           # group by domain
goda catalog --engines           # which real agent engines are installed
goda catalog SecurityAuditor     # show one agent
```

**Pluggable engines.** God-Agent runs on the **OpenAI Agents SDK** by default and
can also run tasks on **CrewAI**, **LangGraph**, **Hermes Agent**, **UI-TARS**,
and **Grok Build** (real adapters), with smolagents and AutoGen detected when
installed. Pick with `goda settings agent.engine hermes`; `goda catalog --engines`
shows what's available, and uninstalled engines fall back to the default. These
are optional extras: `pip install -e '.[crewai]'`, `pip install -e '.[langgraph]'`,
`pip install -e '.[hermes]'`, `pip install -e '.[ui-tars]'`, or
`pip install -e '.[all-engines]'`.

- **Hermes Agent** (Nous Research, MIT) runs God-Agent's task through Hermes's
  real `run_agent` tool-calling loop against the same OpenAI-compatible endpoint,
  with its own persistent memory/skill model enabled. God owns tool dispatch and
  the audit trail, so Hermes's host-level toolsets stay off and it reasons rather
  than acting off-policy.
- **UI-TARS** (ByteDance, Apache-2.0) is a vision-based GUI agent: it screenshots
  the desktop, sends the screenshot + task to a vision LLM, and turns the returned
  action into pyautogui code that actually moves the mouse/keys. Requires a
  vision-capable OpenAI-compatible endpoint (`ui_tars.base_url`/`model`/`api_key`,
  or God's own `llm` provider) and a desktop session. You can also connect the
  UI-TARS desktop app as an MCP server (`config/mcp.example.json`).
- **Grok Build** (xAI) is an agentic coding CLI (`grok`). God runs a task through
  it headlessly (`grok -p "task"`) when the binary is installed, and — because
  xAI's API (`https://api.x.ai/v1`) is OpenAI-compatible — Grok is also available
  as a normal **provider** (`goda providers use Grok`) so Grok models can power
  any engine. Install the CLI with `curl -fsSL https://x.ai/cli/install.sh | bash`.

> **Dependency note:** crewai, langgraph, and hermes-agent each pin their own
> `openai` / `pydantic` release, which can conflict with the `openai-agents`
> default engine's required version. That's a pip-metadata warning (the adapters
> still import and run); if it bothers you, keep the alternate engines in a
> separate virtualenv or select only the one you need at a time. The offline
> heuristic planner requires none of them.

**Connect to external agents via MCP (Model Context Protocol).** MCP is the open
standard for plugging an agent into tools/agents across the internet. Enable it
with `goda settings mcp.enabled true`, then add servers to
`~/.god-agent/mcp.json` (see [`config/mcp.example.json`](config/mcp.example.json)):
stdio (local binary), SSE, or streamable-HTTP (remote) servers. God discovers
their tools at runtime and calls them through its audited pipeline. `goda catalog
--mcp` lists what's configured. This is the honest way to reach "every agent" —
an open registry of MCP servers you point God at, without dependency bloat.

## Browser automation

God-Agent can do real browser tasks: fill in a login form, click through a
dashboard, scrape a page that only renders after JavaScript runs. The built-in
`fetch_url` tool cannot do any of that — it is a bare HTTP GET with **no
JavaScript, no cookies, and no session**. The `browser_*` tools drive an actual
headless Chromium, so pages behave exactly as they do for a human.

```bash
pip install 'god-agent[browser]'      # or: pip install playwright
playwright install chromium           # download the browser binary (once)
```

Then just ask for it:

```bash
goda run "log into https://example.com/app and tell me today's open tickets"
```

### The tools

| Tool | Risk | What it does |
|---|---|---|
| `browser_open` | 3 | Navigate to a URL. Returns final URL, title, status, and rendered text. |
| `browser_click` | 3 | Click an element (CSS selector / text / XPath). |
| `browser_type` | 4 | Type into an input. `press_enter: true` submits; `clear` (default) empties it first. |
| `browser_extract` | 1 | Read rendered `text` / `html` / `value` / `attribute` from the page. |
| `browser_links` | 1 | List every link (text + href), deduplicated — the map for navigating further. |
| `browser_wait` | 1 | Wait for a selector to appear/disappear, or pause. Use before reading async pages. |
| `browser_screenshot` | 2 | Save a PNG (`full_page: true` captures the whole page). |
| `browser_eval` | 5 | Run JavaScript in the page. Escape hatch for dropdowns, scrolling, infinite lists. |
| `browser_stealth_check` | 1 | Report what a bot-detection script sees and how many fingerprint checks pass. |
| `browser_close` | 1 | Close the browser and flush cookies/session to disk. |

### Anti-bot detection

A stock Playwright launch is trivially fingerprinted: `navigator.webdriver` is
`true`, `window.chrome` is missing, the UA contains `HeadlessChrome`, WebGL
reports a software renderer, and CDP leaves `cdc_*` markers on the document.
Any one of them is enough to get flagged.

Stealth mode is **on by default** and patches roughly a dozen signals — but the
part that matters is that it keeps them **mutually consistent**. A UA claiming
Chrome on Windows alongside `navigator.userAgentData.platform === "Linux"` and
a Mesa WebGL renderer is *more* suspicious than no patch at all, because no real
browser produces that combination. Everything derives from one device profile:

```bash
goda settings browser.stealth.profile windows-chrome   # | macos-chrome | linux-chrome
```

Verify rather than assume — point it at a fingerprinting page and read the report:

```
$ goda run "open https://example.com and run a stealth check"

stealth: on  profile: windows-chrome  ->  13/13  [CLEAN]
PASS  navigator.webdriver             undefined
PASS  'webdriver' in navigator        False
PASS  window.chrome present           True
PASS  no headless UA token            clean
PASS  plugins populated               5 plugins
PASS  languages set                   en-US,en
PASS  hardwareConcurrency             8
PASS  deviceMemory                    8
PASS  window chrome offset            outer-inner=85px
PASS  no cdc_ markers                 0 markers
PASS  permissions toString native     True
PASS  platform consistency            platform=Win32 uaData=Windows
PASS  WebGL not software              ANGLE (Intel, Intel(R) UHD Graphics 620 ...)
```

**What it defeats:** passive fingerprinting — the browser stops advertising
that it is automated. Also included is *humanized* interaction: random
per-keystroke delays, cursor movement in steps with jitter rather than
teleporting, and idle pauses between actions, since machine-cadence requests
are a signal in themselves.

**What it does not do:** it will not solve CAPTCHAs, and it will not rotate
through proxy or identity pools. Those systems exist specifically to stop
abuse; circumventing them is a different thing from not looking like a default
headless browser. Use this to test your own defences, drive your own accounts,
and automate against services you are permitted to automate. `browser.proxy`
accepts a **single static** proxy (corporate egress, geo-testing) — it is
deliberately not a rotation pool.

Being invisible is not the same as being polite, so request pacing is on
independently: `browser.polite` honours `robots.txt` and rate-limits to 30
requests/minute per host by default. Both can be switched off.

```jsonc
"browser": {
  "stealth": {
    "enabled": true,
    "profile": "windows-chrome",
    "humanize": {
      "enabled": true,
      "typing_delay_ms": [40, 130],   // random delay per keystroke
      "mouse_steps": [8, 25],         // intermediate cursor move steps
      "pause_ms": [300, 1200]         // idle between actions
    }
  },
  "proxy": { "server": "", "username": "", "password": "", "bypass": "" },
  "polite": {
    "enabled": true,
    "robots_txt": true,
    "user_agent_token": "GodAgent",
    "min_delay_ms": 1000,
    "max_requests_per_minute": 30
  }
}
```

### Why it's a toolset and not an MCP server

You can also point God at an external browser MCP server, but the MCP module's
own docstring is explicit about the trade-off: *"MCP tools are external; they
cannot be graded by the local risk engine."* MCP calls are audited but **not
risk-checked**, and a browser is precisely the tool that can POST your data
anywhere. The built-in tools are graded (`TOOL_RISK`), gated by
`policy.network.enabled`, bounded by config, and written to the hash-chained
audit log like everything else.

### Session behaviour

One browser context is reused across tool calls, so a login typed in step 2 is
still authenticated in step 7 — the thing that makes multi-step tasks possible.
Cookies and localStorage are flushed to `<state.root>/browser_state.json`, so
sessions survive even a restart. `browser_close` tears the session down.

### Configuration

```jsonc
"browser": {
  "headless": true,            // false = visible window (needs a display)
  "timeout_s": 30,             // per-action / navigation timeout
  "max_text_chars": 200000,    // cap on extracted text returned to the model
  "viewport": { "width": 1280, "height": 900 },
  "user_agent": "",            // empty = Playwright's default Chromium UA
  "allow_js": true,            // set false to disable browser_eval entirely
  "state_path": ""             // empty = <state.root>/browser_state.json
}
```

Playwright stays an **optional** dependency: God-Agent's core keeps
`dependencies = []`. Without it the browser tools return an actionable install
hint instead of failing at startup, and the offline heuristic planner is
unaffected.

> **Not the same as the UI-TARS engine.** UI-TARS is an *engine* that replaces
> the whole reasoning loop with a screenshot→vision→pyautogui cycle, and it
> needs a display plus a vision-capable endpoint. The browser tools are ordinary
> *tools* inside the normal loop — they compose with shell, files, and the
> Brain, and they run headless on a server.

## Search, vision, image generation, and speech

A sysadmin agent with only a shell is missing what a general-purpose agent can
do: look things up, see images, draw diagrams, and speak. Four tools close
that gap — and like everything else, they are policy-graded and audited.

```bash
goda run "search for why nginx returns 502 and summarise the fix"
goda run "screenshot the status page and tell me what it shows"
goda run "generate a diagram of our three-tier network topology"
goda run "check disk space and speak an alert if anything is over 90%"
```

### Web search

Works **with zero configuration** — the default DuckDuckGo backend needs no API
key. Upgrade to a real search API when you want better results:

| Backend | Needs | Notes |
|---|---|---|
| `duckduckgo` | nothing | default; scrapes the HTML endpoint |
| `brave` | `search.api_key` | Brave Search API |
| `tavily` | `search.api_key` | Tavily Search API |
| `searxng` | `search.base_url` | self-hosted SearXNG |
| `mock` | nothing | offline stub for tests/demos |

Search composes with the browser tools: search to discover, then
`browser_open` / `browser_extract` to read.

```jsonc
"search": {
  "enabled": true,
  "backend": "duckduckgo",   // duckduckgo | brave | tavily | searxng | mock
  "api_key": "", "base_url": "",
  "max_results": 8, "timeout_s": 20, "safe_search": true
}
```

### Vision, images, and speech

These ride the **same OpenAI-compatible endpoint** God-Agent already uses for
chat, so there is nothing new to install and no new dependency:

| Tool | API surface |
|---|---|
| `image_analyze` | `POST /chat/completions` (multimodal message) |
| `image_generate` | `POST /images/generations` |
| `speak` | `POST /audio/speech` |

Leave `media.base_url` and `media.api_key` empty to inherit from the `llm`
section; set them to point these somewhere else. If your provider does not
implement one of those surfaces you get a clear "does not implement" error
rather than a stack trace.

**Vision is the missing half of `browser_screenshot`.** The browser toolset
could capture a page but not read one — the agent was screenshotting pages it
could not see. Now:

```bash
goda run "open https://example.com, screenshot it, and describe what you see"
```

**Privacy:** `image_analyze` uploads the image to the configured endpoint. For
a dashboard screenshot containing credentials, that means sending it to a third
party. It only runs when called, it is audited, and it dies with
`policy.network.enabled`.

```jsonc
"media": {
  "enabled": true,
  "base_url": "",           // empty = llm.base_url
  "api_key": "",            // empty = llm api key
  "vision_model": "",       // empty = llm.model (must be vision-capable)
  "image_model": "gpt-image-1", "image_size": "1024x1024",
  "speech_model": "tts-1", "speech_voice": "alloy", "speech_format": "mp3",
  "output_dir": "",         // empty = <state.root>/media
  "max_image_mb": 20, "play_audio": false
}
```

Generated images and audio are written to `output_dir`
(`~/.god-agent/media` by default) and the path is returned, so the agent can
hand it to the next step.

### Risk grades

| Tool | Risk | Why |
|---|---|---|
| `web_search` | 3 | outbound network, like `fetch_url` |
| `image_analyze` | 3 | uploads a file to a third party |
| `image_generate` | 3 | outbound network + spend |
| `speak` | 2 | local output, but text goes to a remote TTS endpoint |

All four are gated by `policy.network.enabled`.

## Documents with images

God-Agent could produce text and it could produce images, but it had no way to
**put them together**. "Screenshot the dashboard, search for the error, and
write me a report" ended in a pile of loose files.

A document is an ordered list of **blocks** (heading / text / code / quote /
image / page break) stored as JSON, next to an assets folder holding its
images. Editing is by block index, so the agent can revise one paragraph
without regenerating everything around it.

```bash
goda run "screenshot the status page, then write me a PDF incident report with the screenshot and a summary"
```

### The tools

| Tool | What it does |
|---|---|
| `doc_create` | Create a document (title, assets folder) |
| `doc_add_heading` | Heading, level 1–6 |
| `doc_add_text` | Paragraph |
| `doc_add_code` | Fenced code block |
| `doc_add_quote` | Block quote |
| `doc_add_page_break` | Page break (pdf/docx) |
| `doc_add_image` | **Embed an image** — copies it into the document's assets |
| `doc_outline` | List blocks with indices (run before editing) |
| `doc_edit` | Replace one block's content, in place |
| `doc_remove` | Delete a block |
| `doc_render` | Render to `md` / `html` / `pdf` / `docx` |

### Why a block model instead of "just write a file"

Because revising is the hard part. With a raw file, fixing paragraph 3 means
regenerating the whole document — and re-embedding every image. With blocks:

```
$ goda run "fix the summary paragraph in report"

[0] heading     Summary
[1] text        Disk filled up at 03:14 UTC.        <- edit just this
[2] code        df -h
[3] image       dash.png  Dashboard at 03:14
```

```bash
goda run "open report, edit block 1 with the corrected timeline, re-render to pdf"
```

One document renders to **every** format, so re-rendering after an edit is
cheap and nothing gets rebuilt by hand.

### Rendering

| Format | Needs | Notes |
|---|---|---|
| `md` | nothing | references `assets/…` relatively — stays readable and diffable |
| `html` | nothing | single self-contained file, images base64-embedded |
| `pdf` | Playwright + Chromium | HTML printed through the headless browser the tools already use |
| `docx` | `pip install 'god-agent[docs]'` | real `.docx` with pictures |

Images are base64-embedded in HTML and PDF because those are meant to be
emailed as one file. Markdown deliberately does **not** inline them — a
markdown file with a 500 KB image blob pasted into it is unreadable, and the
assets folder already sits right beside it.

### Image editing

The step between `browser_screenshot` / `image_generate` and `doc_add_image`:

```bash
goda run "resize the screenshot to 800px wide and drop it in the report"
goda run "compose the before and after screenshots side by side"
```

```jsonc
"docs": {
  "enabled": true,
  "default_format": "md",
  "embed_images": true,      // html/pdf only; md always references files
  "pdf_format": "A4",
  "max_image_mb": 25,
  "output_dir": ""           // empty = render next to the document
},
"imaging": { "enabled": true, "max_image_mb": 25, "jpeg_quality": 90 }
```

`image_edit` operations apply in a fixed order — crop → resize → rotate →
flip → grayscale → convert — and the source file is never modified unless you
pass it as `out`.

### Risk grades

Reading (`doc_outline`, `image_info`) is 1. Adding blocks is 2. Anything that
writes to disk (`doc_create`, `doc_edit`, `doc_remove`, `image_edit`,
`image_compose`) is 3, matching `write_file`. These are local file operations,
so unlike the search and media tools they are **not** gated by
`policy.network.enabled`.

## Troubleshooting

| Issue | Cause | Solution |
|---|---|---|
| `goda: command not found` | PATH not updated in current terminal | Run `export PATH="$HOME/.local/bin:$PATH"` (or open a new terminal). For `sudo` installs, launchers are installed in `/usr/local/bin`. |
| Desktop reports missing Tk | Linux Python does not include the Tk package | Ubuntu/Debian: `sudo apt install python3-tk`; Fedora: `sudo dnf install python3-tkinter`; Arch: `sudo pacman -S tk`. |
| `Cannot open a desktop display` | Headless server, SSH, or hosted development sandbox | Run on your Linux graphical desktop, or use `goda chat`. |
| `Python >= 3.10 is required` | System `python3` is older than 3.10 | Install Python 3.10+ (e.g. `sudo apt install python3.11`) ensure `python3` resolves to that interpreter, then run `bash install.sh`. |
| `systemctl` failures in Docker / WSL | Container or environment has no systemd init | Use local install: `./install.sh --local` or pass `--no-start`. |
| `Permission denied: ./install.sh` | Script missing executable permission | Run `bash ./install.sh` or `chmod +x ./install.sh`. |
| `no LLM API key — offline mode` | No LLM provider configured yet | Expected behavior — offline diagnostics work without a key. Configure Kira in the native app’s AI provider tab, or set `KIRA_API_KEY` before launching it. |
| Direct execution | Running without installation | Run `python3 god_agent/cli.py` or `python3 -m god_agent`. |
| `ERROR: browser tools require Playwright` | Playwright missing | `pip install 'god-agent[browser]'` then `playwright install chromium`. |
| `Executable doesn't exist at .../chrome-headless-shell` | The Chromium binary was never downloaded | Run `playwright install chromium`. |
| Browser launch fails on a minimal container | Missing shared system libraries | `playwright install --with-deps chromium` (needs root), or use the distro Chromium package. |
| Agent reads a blank/stale page | Content loads asynchronously | Add a `browser_wait` step for the selector before extracting. |
| `browser_stealth_check` shows FAIL rows | Stealth off, or an explicit `user_agent` was set | Set `browser.stealth.enabled` true, or clear `browser.user_agent` so the profile's UA applies. |
| `ERROR: disallowed by .../robots.txt` | The site opts out of automated access | Confirm you are permitted to automate it, then set `browser.polite.robots_txt=false`. |
| `ERROR: rate limit reached for <host>` | More than `max_requests_per_minute` in a minute | Wait, or raise `browser.polite.max_requests_per_minute`. |
| Typing is very slow | Humanized keystroke delay | Lower `browser.stealth.humanize.typing_delay_ms`, or set `humanize.enabled=false`. |
| `ERROR: document already exists` | `doc_create` refuses to clobber | Pass `overwrite=true`, or `doc_outline` the existing one and edit it. |
| `ERROR: no such document` (doc_add_*) | Document was never created | Run `doc_create` first. |
| `ERROR: DOCX rendering needs python-docx` | Optional extra missing | `pip install 'god-agent[docs]'`. |
| `ERROR: PDF rendering needs Playwright` | Browser extra missing | `pip install 'god-agent[browser]' && playwright install chromium`. |
| `ERROR: image editing needs Pillow` | Pillow missing | `pip install pillow` (or `pip install 'god-agent[docs]'`). |
| Images missing from rendered markdown | Relative refs resolve against the assets folder | Keep the `.assets` folder next to the `.md`, or render `html`/`pdf` instead. |
| `ERROR: search is disabled` | `search.enabled` is false | Set `goda settings search.enabled true`. |
| `ERROR: brave backend needs search.api_key` | Keyed backend with no key | Supply `search.api_key`, or switch to the keyless `duckduckgo` backend. |
| `ERROR: this provider does not implement /images/generations` | Endpoint lacks that surface | Point `media.base_url` at a provider that offers it. |
| `ERROR: no API key configured` (media) | No chat key set | Set the LLM key (`KIRA_API_KEY` by default) or `media.api_key` explicitly. |
| `image_analyze` returns "no description" | Model is not vision-capable | Set `media.vision_model` to a multimodal model. |

## Policy — read & tune before going to production

```bash
sudo nano /etc/god-agent/config.json
# autonomy:  autonomous (default, native) | supervised | sovereign
# approval:  auto (default) | ask | deny
# sandbox:   none (default — native) | docker | local
# execution.memory_limit_mb / cpu_limit_s / max_processes = -1 → unlimited
# evolution.enabled / auto_apply
# network.enabled
# brain.* (path, encryption, auto_write, inject_prompts)
```

- `autonomous` — the agent acts natively on its own; still audited. **Default.**
- `supervised` — high-risk actions ask the operator.
- `sovereign` — everything auto-approved **except the Constitution** (and the
  few catastrophic-command floors that are Constitution-level, not policy).

Safety details and hardening: [docs/SAFETY.md](docs/SAFETY.md).
Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
The Brain: [docs/BRAIN.md](docs/BRAIN.md).
The honest word on consciousness: [docs/CONSCIOUSNESS.md](docs/CONSCIOUSNESS.md).

## Repository layout

```
god_agent/          the agent (zero third-party runtime dependencies for the
                    core; the optional real engine adds openai-agents)
  loop.py           reasoning loop: perceive → plan → act → reflect → remember
  sdk_agent.py      real agent engine bridge (OpenAI Agents SDK) + tool/schema
  swarm.py          God orchestrator operating as a body (God + 6 body parts)
  catalog.py        data-driven agent roster (100 specialists) + engine detection
  body.py           compress the 100 specialists into 6 body parts (Brain/Hands/Legs/Torso)
  engines.py        optional alternate agent engines (CrewAI/LangGraph/Hermes/UI-TARS/Grok/smolagents/AutoGen)
  mcp_servers.py    MCP client: connect external agent/tool servers (any transport)
  brain.py          THE BRAIN — credentials (AES-256), prompts, facts, learnings
  policy.py         Constitution + risk/approval engine
  evolution.py      guarded self-modification pipeline
  memory.py         episodes, reflections, TF-IDF recall
  selfmodel.py      operational self-model
  audit.py          hash-chained tamper-evident log
  desktop.py        native desktop controller + entry point (no HTTP server)
  desktop_ui.py     Tk window, chat, provider settings, approval dialogs
  api.py            optional HTTP API
  static/           dashboard
  tools/            shell, files, system (GPU/process/sysctl/cron), memory,
                    brain, network, browser (headless Chromium), stealth
                    (anti-bot-detection), search (web), media (vision/image/
                    speech), docs (block-model documents), imaging (edit),
                    self, evolve
install.sh          installer (--desktop / --local / system-wide)
install_local.sh    user app, launchers, and Linux application-menu integration
uninstall.sh        removal
tests/              pytest suite
docs/               constitution, brain, safety, architecture, consciousness
```

## Status & roadmap

- [x] Native, unsandboxed full system control (RAM/CPU/GPU, no caps)
- [x] The Brain (two-writer rule, encrypted credentials, auto-write on learning)
- [x] Native Linux desktop app (no browser/localhost server), with Kira defaults
- [x] Chat interface (CLI + web) and full settings menu (`settings` / ⚙)
- [x] Custom API providers (any OpenAI-compatible endpoint, Anthropic, offline)
- [x] Core loop, tools, memory, self-model, audit, policy
- [x] Evolution pipeline (validate → test → apply → rollback)
- [x] One-command installer (local or system-wide) + systemd service
- [x] Offline heuristic mode (no LLM key needed)
- [x] Multi-agent coordination / swarms (God orchestrator + data-driven specialist crew)
- [x] Pluggable agent engines (OpenAI Agents SDK default; CrewAI/LangGraph/Hermes/UI-TARS/Grok/smolagents/AutoGen adapters when installed)
- [x] One body, not a crew — 100 specialists compressed into 6 parts (1 brain, 2 hands, 2 legs, 1 torso)
- [x] Extensible agent registry (`goda catalog`, `~/.god-agent/agents.json`, `agents.d/`)
- [x] MCP client (connect external agent/tool servers: stdio, SSE, streamable-HTTP)
- [x] Real browser automation (headless Chromium: JS, cookies, sessions; policy-graded + audited)
- [x] Anti-bot-detection hardening (consistent device profiles, humanized input, `browser_stealth_check` verification)
- [x] Web search (keyless DuckDuckGo default + Brave/Tavily/SearXNG), vision, image generation, speech
- [x] Documents with images (block model, edit in place, render md/html/pdf/docx) + image editing
- [ ] Real-time alert rules (thresholds on metrics)
- [ ] Vault/KMS integration for Brain credentials

## License

MIT. Use responsibly: with root comes great accountability. The audit log and
the operator are the ultimate authorities.
