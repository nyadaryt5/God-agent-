"""Tamper-evident append-only audit log.

Each record links to the previous one via a SHA-256 hash (a hash chain), so
deleting or editing any past record is detectable with `goda audit --verify`.
The log is never truncated or modified in place; rotation is handled by the
operator (or a scheduled `goda audit --rotate`).
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from .utils import canonical, now_iso, sha256_text


class AuditLog:
    def __init__(self, path: str, max_mb: int = 256):
        self.path = os.path.abspath(os.path.expanduser(path))
        self.max_mb = max_mb
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    # -- write -----------------------------------------------------------
    def append(self, actor: str, action: str, summary: str, details: Optional[dict] = None,
               outcome: str = "ok") -> dict:
        prev_hash = self._last_hash()
        record = {
            "seq": self._seq() + 1,
            "ts": now_iso(),
            "actor": actor,
            "action": action,
            "summary": summary[:500],
            "details": details or {},
            "outcome": outcome,
            "prev_hash": prev_hash,
        }
        body = canonical({k: v for k, v in record.items() if k != "hash"})
        record["hash"] = sha256_text(body)
        line = json.dumps(record, ensure_ascii=False)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return record

    # -- verify ----------------------------------------------------------
    def verify(self) -> tuple[bool, list[str]]:
        errors: list[str] = []
        if not os.path.isfile(self.path):
            return True, ["(audit log is empty)"]
        prev = ""
        with open(self.path, "r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    errors.append(f"line {lineno}: not valid JSON")
                    continue
                body = canonical({k: v for k, v in rec.items() if k != "hash"})
                if sha256_text(body) != rec.get("hash"):
                    errors.append(f"line {lineno}: hash mismatch")
                if rec.get("prev_hash") != prev:
                    errors.append(f"line {lineno}: chain broken (prev hash mismatch)")
                prev = rec.get("hash", "")
        return (len(errors) == 0), errors

    # -- readers ---------------------------------------------------------
    def entries(self, limit: int = 200) -> list[dict]:
        if not os.path.isfile(self.path):
            return []
        lines = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    lines.append(line)
        out = []
        for line in lines[-limit:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def count(self) -> int:
        if not os.path.isfile(self.path):
            return 0
        with open(self.path, "r", encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())

    def _last_hash(self) -> str:
        if not os.path.isfile(self.path):
            return ""
        last = ""
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    last = line
        if not last:
            return ""
        try:
            return json.loads(last).get("hash", "")
        except json.JSONDecodeError:
            return ""

    def _seq(self) -> int:
        if not os.path.isfile(self.path):
            return 0
        with open(self.path, "r", encoding="utf-8") as fh:
            last = ""
            for line in fh:
                line = line.strip()
                if line:
                    last = line
        if not last:
            return 0
        try:
            return int(json.loads(last).get("seq", 0))
        except (json.JSONDecodeError, TypeError, ValueError):
            return 0

    def size_mb(self) -> float:
        if not os.path.isfile(self.path):
            return 0.0
        return os.path.getsize(self.path) / (1024 * 1024)

    def rotate(self, backup=True) -> Optional[str]:
        if not os.path.isfile(self.path):
            return None
        target = self.path
        if backup:
            target = f"{self.path}.{now_iso().replace(':', '-')}.bak"
            os.rename(self.path, target)
        else:
            os.remove(self.path)
        return target
