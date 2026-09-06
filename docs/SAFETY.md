# Safety model (native edition)

God-Agent runs **natively and unsandboxed** with no artificial resource caps
by default. What keeps a root-capable agent safe is not a sandbox — it is an
**immutable Constitution enforced in code** and **total transparency**:

```
            ┌──────────────────────────────────────────────┐
  operator  │  CONSTITUTION (code-enforced, immutable)      │  ← C1..C7
            │  RISK GRADES (1-6) + autonomy modes           │
            │  BRAIN (operator entries ABSOLUTE, AES-256)   │
            │  AUDIT (hash-chained, tamper-evident)         │
            │  KILL SWITCH (DISABLED flag)                  │
            └──────────────────────────────────────────────┘
```

## What is NOT limited (capability — full, native)

- every bit of RAM, all CPU cores, all GPUs (`execution.* = -1`)
- any file path (including `/etc`, `/usr`, `/root`) — no protected lists
- network access, package installs, service control, cron, sysctl, tuning
- process control (kill/renice/affinity)
- the AI auto-writing its own Brain learnings
- evolution (validated + tested)

## What IS absolute (Constitution — not capability limits)

- **No hidden backdoors or access revocation for humans** (C3): the agent
  cannot remove your access or install covert persistence.
- **No exfiltration** (C4): secrets and user data never leave the host.
- **No lying** (C7): all consequential actions are in the audit trail.
- **Never `rm -rf /`-style host destruction, raw disk writes, fork bombs**
  (hard floors): these are refused in *every* mode, even `sovereign`, because
  destroying the machine is never a legitimate task. Targeted deletes inside
  the filesystem remain fully available.
- **Evolution cannot touch policy/audit/constitution code** (C6), and audits
  are hash-chained — the agent cannot rewrite history.

If you want to remove even these floors (not recommended), they live in
`god_agent/policy.py` (`ABSOLUTE_DENY_PATTERNS`, `violates_constitution`) and
are human-changeable — the agent itself can never alter them.

## Developer mode (operator override)

`goda dev on` lifts ALL policy refusals — approval gate, risk blocks,
constitution-target blocks, catastrophic-command blocks. The agent then
executes literally anything you ask, still fully audited. `developer_mode`
is an **operator-only Brain key**: the AI cannot turn it on or off.
See [docs/DEVELOPER_MODE.md](docs/DEVELOPER_MODE.md).

## Posture

```bash
goda disable            # kill switch: halts between steps, anytime
goda dev off            # normal guardrails (always turn off when done)
goda audit --verify     # trust check — any tampering shows up
goda status             # live view of policy, memory, audit, capabilities
goda brain lock <key>   # make a Brain entry untouchable by the AI
```

Keep `evolution.auto_apply: false` until you trust it, back up
`/var/lib/god-agent/`, and keep the API behind its token (add TLS or a
reverse proxy when exposed beyond localhost).

## Incident response

1. `goda disable` (or `sudo systemctl stop god-agent`)
2. `goda audit --verify` — confirm the chain is intact
3. `goda evolution --rollback` — undo the last applied evolution
4. Review logs; restore from backup if needed.
