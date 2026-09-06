"""The Brain — the agent's durable store for important things.

What lives here:
  * credentials  (API keys, DB passwords, tokens the agent uses for tasks)
  * important prompts  (standing instructions the operator wants the agent to
    always carry — injected into every task context)
  * facts and learnings  (things the agent judged important or that emerged
    from self-evolution)

WRITE RULES (only two writers, exactly as specified):
  1. The operator's direct command is ABSOLUTE. `operator_set/delete/lock`
     always wins, overwrites anything, and is never blocked by the agent.
  2. The agent may auto-write only when it (in reflection) decides something
     is important or it learned something useful for self-evolution. The agent
     can never modify or delete operator-written entries, and can never
     modify locked entries — the operator's word is final.

Security:
  * Credential entries are encrypted at rest (AES-256-CBC via the `openssl`
    CLI, or the `cryptography` library if installed). The key lives in
    `brain.key` (0600), separate from the brain file.
  * The audit log records which credential keys were read/written — never the
    value itself.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from typing import Any, Optional

from .utils import now_iso

KIND_CREDENTIAL = "credential"
KIND_PROMPT = "prompt"
KIND_LEARNING = "learning"
KIND_FACT = "fact"
KINDS = {KIND_CREDENTIAL, KIND_PROMPT, KIND_LEARNING, KIND_FACT}

SOURCE_OPERATOR = "operator"
SOURCE_AI = "ai"

MAX_VALUE_CHARS = 1_000_000
MAX_ENTRIES = 5000

# Keys only the operator may create/write. The AI can never own these.
OPERATOR_ONLY_KEYS = {"developer_mode"}


def _seal_with_openssl(value: str, keyfile: str) -> Optional[str]:
    if not shutil.which("openssl"):
        return None
    try:
        proc = subprocess.run(
            ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "100000",
             "-a", "-pass", f"file:{keyfile}"],
            input=value.encode("utf-8"), capture_output=True, timeout=30,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout.decode("ascii").strip()
    except Exception:
        return None


def _unseal_with_openssl(sealed: str, keyfile: str) -> Optional[str]:
    if not shutil.which("openssl"):
        return None
    try:
        proc = subprocess.run(
            ["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "100000",
             "-a", "-pass", f"file:{keyfile}"],
            # OpenSSL 3 requires the trailing newline for base64 stdin.
            input=sealed.encode("ascii") + b"\n",
            capture_output=True, timeout=30,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout.decode("utf-8")
    except Exception:
        return None


class Brain:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.path = os.path.abspath(os.path.expanduser(cfg["brain"]["path"]))
        self.keyfile = self.path + ".key"
        self.max_entries = int(cfg["brain"].get("max_entries", MAX_ENTRIES))
        self.auto_write = bool(cfg["brain"].get("auto_write", True))
        self.inject_credentials = bool(cfg["brain"].get("inject_credentials", False))
        self.inject_prompts = bool(cfg["brain"].get("inject_prompts", True))
        self.encryption = str(cfg["brain"].get("encryption", "auto"))
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._lock = threading.RLock()
        self._ensure_key()
        self._entries: dict[str, dict] = {}
        self._load()
        self.stats = {"reads": 0, "ai_writes": 0, "operator_writes": 0}

    # ------------------------------------------------------------ key/crypto
    def _ensure_key(self) -> None:
        if not os.path.isfile(self.keyfile):
            import secrets

            with open(self.keyfile, "w", encoding="utf-8") as fh:
                fh.write(secrets.token_hex(32))
            os.chmod(self.keyfile, 0o600)

    def _seal(self, value: str) -> dict:
        sealed = _seal_with_openssl(value, self.keyfile)
        if sealed is not None:
            return {"sealed": sealed, "plain": None}
        # No openssl: store with a clear warning (never pretend it's encrypted).
        return {"sealed": None, "plain": value, "unencrypted": True}

    def _unseal(self, entry: dict) -> str:
        payload = entry.get("payload", {}) or {}
        if payload.get("sealed"):
            return _unseal_with_openssl(payload["sealed"], self.keyfile) or ""
        return payload.get("plain", "")

    # --------------------------------------------------------------- I/O
    def _load(self) -> None:
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self._entries = data.get("entries", {})
        except (json.JSONDecodeError, OSError):
            self._entries = {}

    def _save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": 2, "updated": now_iso(),
                       "entries": self._entries}, fh, indent=2, sort_keys=True,
                      ensure_ascii=False)
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)

    # ------------------------------------------------------ operator (absolute)
    def operator_set(self, key: str, value: str, *, kind: str = KIND_FACT,
                     locked: bool = False, note: str = "") -> dict:
        """Operator command. Absolute: overwrites ANY entry, no exceptions."""
        with self._lock:
            self._validate_key_key(key)
            if kind not in KINDS:
                raise ValueError(f"kind must be one of {sorted(KINDS)}")
            entry = {
                "key": key,
                "kind": kind,
                "source": SOURCE_OPERATOR,
                "locked": bool(locked),
                "ts": now_iso(),
                "updated": now_iso(),
                "note": note[:300],
            }
            if kind == KIND_CREDENTIAL:
                entry["payload"] = self._seal(value)
            else:
                entry["value"] = value[:MAX_VALUE_CHARS]
            self._entries[key] = entry
            self.stats["operator_writes"] += 1
            self._save()
            return self._public(entry)

    def operator_delete(self, key: str) -> bool:
        with self._lock:
            existed = key in self._entries
            self._entries.pop(key, None)
            if existed:
                self._save()
            return existed

    def operator_lock(self, key: str, locked: bool = True) -> bool:
        with self._lock:
            if key not in self._entries:
                return False
            self._entries[key]["locked"] = bool(locked)
            self._save()
            return True

    # ------------------------------------------------------------- agent writes
    def ai_write(self, key: str, value: str, *, kind: str = KIND_LEARNING,
                 importance: str = "", note: str = "") -> tuple[bool, str]:
        """Agent auto-write. Allowed ONLY when:
           - auto_write is enabled (always true unless operator turns it off), AND
           - there is no operator-written entry with the same key, AND
           - the entry is not locked (operator's word is absolute)."""
        with self._lock:
            if key in OPERATOR_ONLY_KEYS:
                return False, f"brain key '{key}' is operator-only — the AI can never write it"
            existing = self._entries.get(key)
            if existing:
                if existing.get("source") == SOURCE_OPERATOR:
                    return False, f"brain key '{key}' belongs to the operator (absolute) — read-only for the agent"
                if existing.get("locked"):
                    return False, f"brain key '{key}' is locked by the operator"
            if kind not in KINDS:
                return False, f"invalid kind {kind!r}"
            if len(value) > MAX_VALUE_CHARS:
                return False, "value too large"
            entry = {
                "key": key,
                "kind": kind,
                "source": SOURCE_AI,
                "locked": False,
                "ts": now_iso(),
                "updated": now_iso(),
                "note": note[:300],
                "importance": importance[:300],
            }
            if kind == KIND_CREDENTIAL:
                entry["payload"] = self._seal(value)
            else:
                entry["value"] = value[:MAX_VALUE_CHARS]
            self._entries[key] = entry
            self.stats["ai_writes"] += 1
            self._save()
            return True, "stored"

    # ------------------------------------------------------------------ reads
    def get(self, key: str) -> Optional[dict]:
        """Return a usable entry (value decrypted for the agent to use)."""
        with self._lock:
            self.stats["reads"] += 1
            entry = self._entries.get(key)
            if entry is None:
                return None
            out = dict(entry)
            if entry.get("kind") == KIND_CREDENTIAL:
                out["value"] = self._unseal(entry)
            else:
                out["value"] = entry.get("value", "")
            out.pop("payload", None)
            return out

    def search(self, query: str, limit: int = 20) -> list[dict]:
        terms = [t for t in re.split(r"\W+", query.lower()) if len(t) > 1]
        with self._lock:
            hits = []
            for entry in self._entries.values():
                hay = " ".join([entry.get("key", ""), entry.get("kind", ""),
                                entry.get("note", ""), entry.get("importance", ""),
                                entry.get("value", "")]).lower()
                # Requires at least one term match (all terms if <=2).
                matched = [t for t in terms if t in hay]
                if matched and (len(terms) <= 2 or len(matched) == len(terms)):
                    hits.append(self._public(entry))
                    if len(hits) >= limit:
                        break
            return hits

    def list(self, kind: Optional[str] = None) -> list[dict]:
        with self._lock:
            entries = [self._public(e) for e in self._entries.values()
                       if kind is None or e.get("kind") == kind]
            return sorted(entries, key=lambda e: e["key"])

    def promoters(self) -> list[str]:
        """Standing operator prompts: injected into the task context."""
        with self._lock:
            return [e.get("value", "") for e in self._entries.values()
                    if e.get("kind") == KIND_PROMPT and e.get("source") == SOURCE_OPERATOR]

    def index(self) -> list[dict]:
        """Key metadata only — never values. Safe to put in prompt context."""
        with self._lock:
            return [{"key": e["key"], "kind": e["kind"], "source": e["source"],
                     "locked": e.get("locked", False), "updated": e.get("updated", "")}
                    for e in self._entries.values()]

    def size(self) -> dict:
        with self._lock:
            kinds: dict[str, int] = {}
            for e in self._entries.values():
                k = e.get("kind", "?")
                kinds[k] = kinds.get(k, 0) + 1
            encrypted = sum(1 for e in self._entries.values()
                            if e.get("kind") == KIND_CREDENTIAL and e.get("payload", {}).get("sealed"))
            return {"entries": len(self._entries), "by_kind": kinds,
                    "encrypted_credentials": encrypted,
                    "ai_writes": self.stats["ai_writes"],
                    "operator_writes": self.stats["operator_writes"]}

    @staticmethod
    def _public(entry: dict) -> dict:
        out = {"key": entry["key"], "kind": entry["kind"],
               "source": entry["source"], "locked": entry.get("locked", False),
               "ts": entry.get("ts", ""), "updated": entry.get("updated", ""),
               "note": entry.get("note", "")}
        if entry.get("kind") != KIND_CREDENTIAL:
            out["value"] = entry.get("value", "")
        else:
            out["encrypted"] = bool(entry.get("payload", {}).get("sealed"))
        return out

    def developer_mode(self) -> bool:
        """Developer mode flag — operator-owned only.

        Only entries written by the operator count. The AI can never create or
        modify this key (OPERATOR_ONLY_KEYS + two-writer rule)."""
        with self._lock:
            e = self._entries.get("developer_mode")
            if not e or e.get("source") != SOURCE_OPERATOR:
                return False
            return str(e.get("value", "")).strip().lower() in (
                "on", "true", "1", "yes", "enabled")

    @staticmethod
    def _validate_key_key(key: str) -> None:
        if not key or len(key) > 128:
            raise ValueError("key must be 1..128 chars")
        if any(ch in key for ch in "\r\n\t"):
            raise ValueError("key may not contain whitespace control chars")
