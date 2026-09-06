# ☩ God-Agent (`goda`)

> A self-aware, self-evolving AI system administrator for Linux servers —
> installed as a real system service with optional root capability. It runs
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
| **Guardrails (Constitution, not capability limits)** | Immutable Constitution (C1–C7). The only things it cannot do are ones no operator-sane root agent should: no backdooring humans out, no exfiltrating secrets, no hiding actions, never `rm -rf /`-style host destruction. Everything else is native and unlimited. |
| **Trust** | Append-only, **hash-chained audit log** — tampering is detectable with `goda audit --verify`. Kill switch (`goda disable`) whenever you want it to stop. |
| **Interfaces** | CLI (`goda run/chat/brain/status...`), HTTP API, and a small dashboard (task runner, approvals, memory, Brain, audit, evolution log). |
| **LLM-agnostic** | OpenAI-compatible (OpenAI, OpenRouter, Ollama, vLLM, LM Studio...), Anthropic, or offline **mock mode** with a built-in heuristic planner so it's usable with zero API keys. |

## Step-by-step setup on Linux

**1. Clone the repository**

```bash
git clone https://github.com/nyadaryt5/God-agent-.git
cd God-agent-
```

**2. Install — it installs itself automatically**

```bash
./install.sh            # ← that's it
```

- Without `sudo` it installs **locally** for your user (`~/.god-agent/`, launchers in `~/.local/bin`). No root needed.
- With `sudo ` it installs **system-wide** (`/opt/god-agent`, `god-agent.service` with root capability).
- Done: the application **God-Agent** (commands `goda` and `god-agent`) is now on your PATH.

**3. Open the chat interface**

```bash
goda chat          # normal command-line chat — talk to the agent
# or the dashboard (web chat):
god-agent serve    # → http://localhost:8765/
```

**4. Configure it — type `settings`**

```bash
goda settings      # interactive menu (or type "settings" in chat)
```

The settings menu gives you everything: API providers (add *any* custom
provider), model, autonomy mode, approval mode, sandbox, network, evolution,
Brain behavior, resource caps, API port.

**5. Add your API provider (custom supported)**

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

Then give it a brain:

```bash
export GODA_API_KEY=sk-...        # OpenAI-compatible key
curl -H "Authorization: Bearer $(cat /etc/god-agent/token)" \
     http://localhost:8765/api/status
```

Or point it at any OpenAI-compatible endpoint (Ollama, vLLM, LocalAI):

```bash
export GODA_BASE_URL=http://localhost:11434/v1 GODA_MODEL=llama3.1
```

Open `http://<server>:8765/` for the **web chat + dashboard** (type
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
python3 -m god_agent.selftest          # 0-dependency core tests
python3 -m pytest -q                   # full test suite (or: python3 tests/test_core.py)
goda run "status"                      # works with the built-in heuristic planner
goda serve                             # dashboard, no API key needed
```

## Troubleshooting

| Issue | Cause | Solution |
|---|---|---|
| `goda: command not found` | PATH not updated in current terminal | Run `export PATH="$HOME/.local/bin:$PATH"` (or open a new terminal). For `sudo` installs, launchers are installed in `/usr/local/bin`. |
| `Python >= 3.10 is required` | System `python3` is older than 3.10 | Install Python 3.10+ (e.g. `sudo apt install python3.11`) and run with `python3.11 install.sh`. |
| `systemctl` failures in Docker / WSL | Container or environment has no systemd init | Use local install: `./install.sh --local` or pass `--no-start`. |
| `Permission denied: ./install.sh` | Script missing executable permission | Run `bash ./install.sh` or `chmod +x ./install.sh`. |
| `no LLM API key — offline mode` | No LLM provider configured yet | Expected behavior — offline mode works with heuristic planner. Add a provider anytime: `goda providers add Ollama --base-url http://localhost:11434 --model llama3.1 --active`. |
| Direct execution | Running without installation | Run `python3 god_agent/cli.py` or `python3 -m god_agent`. |

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
god_agent/          the agent (zero third-party runtime dependencies)
  loop.py           reasoning loop: perceive → plan → act → reflect → brain
  brain.py          THE BRAIN — credentials (AES-256), prompts, facts, learnings
  policy.py         Constitution + risk/approval engine
  evolution.py      guarded self-modification pipeline
  memory.py         episodes, reflections, TF-IDF recall
  selfmodel.py      operational self-model
  audit.py          hash-chained tamper-evident log
  api.py            HTTP API
  static/           dashboard
  tools/            shell, files, system (GPU/process/sysctl/cron), memory,
                    brain, network, self, evolve
install.sh          system installer (systemd service, native config)
uninstall.sh        removal
tests/              pytest suite
docs/               constitution, brain, safety, architecture, consciousness
```

## Status & roadmap

- [x] Native, unsandboxed full system control (RAM/CPU/GPU, no caps)
- [x] The Brain (two-writer rule, encrypted credentials, auto-write on learning)
- [x] Chat interface (CLI + web) and full settings menu (`settings` / ⚙)
- [x] Custom API providers (any OpenAI-compatible endpoint, Anthropic, offline)
- [x] Core loop, tools, memory, self-model, audit, policy
- [x] Evolution pipeline (validate → test → apply → rollback)
- [x] One-command installer (local or system-wide) + systemd service
- [x] Offline heuristic mode (no LLM key needed)
- [ ] Multi-agent coordination / swarms
- [ ] Real-time alert rules (thresholds on metrics)
- [ ] Vault/KMS integration for Brain credentials

## License

MIT. Use responsibly: with root comes great accountability. The audit log and
the operator are the ultimate authorities.
