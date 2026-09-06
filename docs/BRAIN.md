# The Brain

The Brain is the agent's durable store for **important things**: credentials,
standing prompts, facts, and learnings. It survives restarts and is loaded
into every task's context (key index + operator prompts); values are read
on demand.

## The two-writer rule (exactly as specified)

1. **Your direct command is ABSOLUTE.** `goda brain set <key> <value>`, the
   API (`POST /api/brain/set`), or the dashboard can write any key, any time,
   overwrite anything, and lock entries. The agent can never override,
   modify, or delete an operator-written or operator-locked entry.
2. **The agent auto-writes** when it decides something is important or when
   it learned something useful for self-evolution. After each successful task
   it reflects and stores learnings under its own namespace
   (`learning:task:*`). If it later re-learns the same thing it may refine
   its own entries — never yours.

Enforcement is in code (`god_agent/brain.py`), not in prompts — the model
cannot talk its way past it.

## Entry kinds

| kind | purpose | encrypted at rest |
|------|---------|-------------------|
| `credential` | API keys, DB passwords, tokens the agent uses | ✅ AES-256 (openssl) |
| `prompt` | standing instructions the operator wants injected | no (readable) |
| `fact` | durable knowledge the operator confirms | no |
| `learning` | what the AI learned (auto-writable by AI only) | no |

## Commands

```bash
goda brain set db_pass 'secret' --kind credential --lock --note "prod db"
goda brain set rule 'always back up before upgrades' --kind prompt
goda brain show                 # sizes + metadata (values hidden)
goda brain get db_pass          # credentials shown as *** in the CLI
goda brain delete db_pass       # operator only
goda brain lock db_pass         # / --unlock — operator only
goda brain size
```

API: `GET /api/brain`, `GET /api/brain/<key>`, `POST /api/brain/set`,
`POST /api/brain/delete`.

## Agent tools

`brain_read` (decrypts credentials for use in a task — audited),
`brain_search`, `brain_list`, `brain_write` (AI path only).

## Security notes

* Credential values are encrypted with a key in `brain.json.key` (0600,
  separate from the brain file). If `openssl` is unavailable the value is
  stored plaintext with an explicit `unencrypted: true` marker — the agent
  records this, and the installer guarantees openssl presence.
* CLI/API never echo credential values. `brain_read` decrypts inside the
  agent context and the audit log records *that the key was read* — never the
  value.
* The Brain is NOT a replacement for a secret manager; for production,
  consider Vault/sops integration (roadmap).
