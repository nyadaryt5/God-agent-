# Architecture

```
                       ┌─────────────────────────────┐
   user / operator ───▶│ CLI (goda)  ·  HTTP API+UI  │
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
| `god_agent.loop` | agent reasoning loop (plan → act → reflect → brain) |
| `god_agent.brain` | THE BRAIN: two-writer rule, encrypted credentials, auto-write |
| `god_agent.policy` | Constitution + risk grades + decisions |
| `god_agent.executor` | native execution (limits OFF by default; docker optional) |
| `god_agent.tools.*` | shell, files, system (GPU/process/sysctl/cron), memory, brain, network, self, evolve |
| `god_agent.memory` | persistent episodes/reflections, TF-IDF search |
| `god_agent.selfmodel` | operational self-model (guarded updates) |
| `god_agent.evolution` | guarded self-improvement pipeline |
| `god_agent.audit` | tamper-evident append-only log |
| `god_agent.api` | HTTP API + dashboard backend |
| `god_agent.watchdog` | daemon heartbeat + evolution review |
| `god_agent.cli` | `goda` command line |

## Data lifecycle

- memory.db (sqlite WAL) — episodes, reflections, self versions, evolution log
- brain.json + brain.json.key — THE BRAIN (credential values AES-256 encrypted)
- self.json — the self-model (immutable schema fields, agent-editable
  capabilities/notes/lessons)
- audit.jsonl — hash chain; verify with `goda audit --verify`
- evolution/ — candidate trees, patches, rollback snapshots
