# Architecture

```
                       ┌─────────────────────────────┐
   user / operator ───▶│ Desktop · CLI · HTTP API/UI │
                       └──────────────┬──────────────┘
                                      │ task
                       ┌──────────────▼──────────────┐
                       │          AGENT LOOP          │
                       │  PERCEIVE → PLAN → ACT →     │
                       │  REFLECT → BRAIN → REMEMBER  │
                       └───┬────────┬────────┬────────┘
                           │        │        │
              ┌────────────▼──┐ ┌───▼─────┐ ┌▼──────────────┐
              │ SELF-MODEL    │ │ MEMORY  │ │ POLICY ENGINE │
              │ identity,     │ │ TF-IDF  │ │ Constitution  │
              │ capabilities, │ │ search  │ │ risk 1-6      │
              │ stats,        │ │ episodes│ │ denial floors │
              │ reflections   │ │ lessons │ │ kill switch   │
              └───────────────┘ └─────────┘ └──────┬───────┘
              ┌───────────────┐                    │
              │ THE BRAIN     │                    │
              │ credentials   │────────────────────┤
              │ prompts       │  operator = ABSOLUTE│
              │ facts/learnings│ AI = auto (guarded)│
              └───────────────┘                    │
                                                   │
              ┌────────────────────────────────────▼──────┐
              │ NATIVE EXECUTOR (no caps, no sandbox)      │
              │ shell · files · services · packages ·      │
              │ processes · sysctl · cron · tune · GPU     │
              └────────────────────────────────────────────┘
              ┌────────────────────────────────────────────┐
              │ EVOLUTION PIPELINE                          │
              │ validate → build → test → apply → rollback │
              └────────────────────────────────────────────┘
              ┌────────────────────────────────────────────┐
              │ AUDIT LOG (append-only, hash-chained)      │
              └────────────────────────────────────────────┘
```

## Packages

| module | role |
|--------|------|
| `god_agent.loop` | agent loop: perceive → (real engine | heuristic) → act → reflect → remember |
| `god_agent.sdk_agent` | real agent engine bridge: OpenAI Agents SDK model/tool/schema wiring |
| `god_agent.swarm` | God orchestrator operating as a body: God + 6 body-part agents (SDK handoffs) |
| `god_agent.catalog` | data-driven agent roster (built-in 100 + user definitions) + engine detection |
| `god_agent.body` | compress the 100 specialists into 6 body parts (Brain, 2 Hands, 2 Legs, Torso) |
| `god_agent.engines` | pluggable agent engines: OpenAI SDK (default), CrewAI/LangGraph/Hermes/UI-TARS/Grok adapters (functional), smolagents/AutoGen (detected, not implemented) |
| `god_agent.mcp_servers` | MCP client: connect external agent/tool servers (stdio, SSE, streamable-HTTP) |
| `god_agent.brain` | THE BRAIN: two-writer rule, encrypted credentials, auto-write |
| `god_agent.policy` | Constitution + risk grades + decisions |
| `god_agent.executor` | native execution (limits OFF by default; docker optional) |
| `god_agent.tools.*` | shell, files, system (GPU/process/sysctl/cron), memory, brain, network, self, evolve |
| `god_agent.memory` | persistent episodes/reflections, TF-IDF search |
| `god_agent.selfmodel` | operational self-model (guarded updates) |
| `god_agent.evolution` | guarded self-improvement pipeline |
| `god_agent.audit` | tamper-evident append-only log |
| `god_agent.desktop` | native desktop entry point and queued worker controller (no HTTP) |
| `god_agent.desktop_ui` | lazily imported Tk window, provider settings, task output, approvals |
| `god_agent.providers` | Kira defaults, custom profiles, local credentials, model discovery |
| `god_agent.api` | optional HTTP API + dashboard backend |
| `god_agent.watchdog` | daemon heartbeat + evolution review |
| `god_agent.cli` | `goda` command line |

## Data lifecycle

- memory.db (sqlite WAL) — episodes, reflections, self versions, evolution log
- brain.json + brain.json.key — THE BRAIN (credential values AES-256 encrypted)
- self.json — the self-model (immutable schema fields, agent-editable
  capabilities/notes/lessons)
- audit.jsonl — hash chain; verify with `goda audit --verify`
- evolution/ — candidate trees, patches, rollback snapshots

- providers.json — active provider and profiles; optional saved keys use file
  permissions (`0600`), **not Brain encryption**. Kira environment credentials
  are resolved at request time and are not copied into this file.

The native desktop calls the same agent/runtime as the CLI, in-process. A
single worker serializes tasks; an event queue carries progress/results to Tk's
main thread and native dialogs return policy approval decisions. No browser,
HTTP listener, or IPC service is involved. See [DESKTOP.md](DESKTOP.md).
