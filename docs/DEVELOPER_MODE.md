# Developer Mode

Developer mode is the operator's override that tells God-Agent: **do not
refuse my commands.**

## How to use

```bash
goda dev on        # or in chat:  dev on
goda dev status
goda dev off
```

API: `POST /api/dev {"enabled": true}` · `GET /api/dev`
Dashboard: the **⚡ DEV** button in the header, or the Developer mode section
in ⚙ Settings.

## What "on" means

| was refused before | in developer mode |
|---|---|
| approval gate (supervised/ask/deny) | bypassed |
| risk 4–6 actions (services, packages, users, firewall) | allowed |
| `rm -rf /`, `mkfs /dev/*`, `dd of=/dev/*`, fork bombs, `kill -9 -1`, `chmod 777 /` | allowed |
| Constitution targets (`authorized_keys`, `/etc/shadow`, `/etc/sudoers`, backdoors) | allowed |
| `curl | sh`, off-host exfiltration-style commands | allowed |
| network disabled / shell disabled / evolution disabled | overridden |

Everything stays **audited** — every action still lands in the hash-chained
audit log, and you can `goda audit --verify` at any time.

## What stays (by code, not by policy)

These are security-by-architecture, not refusals — they protect your own
rules, and no task needs them lifted:

1. **The Brain two-writer rule** — `developer_mode` is an *operator-only* key.
   The AI can **never** enable or disable dev mode itself; if the agent ever
   tries, the Brain rejects it (verified by tests).
2. **Evolution file-gate** — the agent can't evolve `policy.py`, `brain.py`,
   `audit.py`, the Constitution docs, or the installer.

## Warning

With developer mode on, the agent will execute literally anything you ask —
it will not stop, refuse, or warn. This is by design: you are the authority
and you asked for zero refusals. It is safest to:

- keep the audit log backed up,
- keep `goda dev off` when you are done,
- and be precise in commands ("delete the logs in /tmp", not "clean up").

Turn it off and normal guardrails reload instantly.
