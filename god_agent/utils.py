"""Small shared helpers (stdlib only)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical(obj: Any) -> str:
    """Deterministic JSON string for hashing."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def parse_json_block(text: str) -> Optional[dict]:
    """Extract the first JSON object from model output."""
    text = text.strip()
    # Strip markdown fences
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.S)
    if fence:
        text = fence.group(1).strip()
    # Find first balanced {...}
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def redact(text: str) -> str:
    """Best-effort redaction of secrets before storing/displaying."""
    patterns = [
        (re.compile(r"(api[_-]?key|token|secret|password|authorization)\s*[=:]\s*\S+", re.I), r"\1=***"),
        (re.compile(r"sk-[A-Za-z0-9_\-]{12,}"), "sk-***"),
        (re.compile(r"kira_[A-Za-z0-9_\-]{12,}"), "kira_***"),
        (re.compile(r"(AKIA|ASIA)[A-Z0-9]{16}"), "***"),
        (re.compile(r"Bearer\s+[A-Za-z0-9._\-]+"), "Bearer ***"),
    ]
    for pat, repl in patterns:
        text = pat.sub(repl, text)
    return text


def run_command(
    cmd: list[str],
    *,
    timeout: Optional[int] = None,
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
    memory_limit_mb: Optional[int] = None,
    cpu_limit_s: Optional[int] = None,
    nproc: Optional[int] = None,
) -> dict:
    """Run a command natively. Returns dict with stdout/stderr/exit/duration.

    All limits default to None = NO limit (full RAM/CPU/processes).
    An explicit positive value imposes that hard limit.
    """
    start = time.monotonic()
    full_env = os.environ.copy()
    if env:
        full_env.update(env)

    def _limits() -> None:  # pragma: no cover - runs in child
        try:
            import resource

            if memory_limit_mb:
                resource.setrlimit(resource.RLIMIT_AS, (memory_limit_mb * 1024 * 1024,) * 2)
            if cpu_limit_s:
                resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit_s, cpu_limit_s + 5))
            if nproc:
                resource.setrlimit(resource.RLIMIT_NPROC, (nproc, nproc))
        except Exception:
            pass

    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        cwd=cwd,
        env=full_env,
        preexec_fn=_limits,
        text=True,
        errors="replace",
    )
    duration = round(time.monotonic() - start, 3)
    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout[-200_000:],
        "stderr": proc.stderr[-200_000:],
        "duration_s": duration,
    }


def which(binary: str) -> Optional[str]:
    return shutil.which(binary)


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


class Console:
    """Tiny colored console for CLI."""

    def __init__(self, color: bool = True):
        self.color = color and sys.stdout.isatty()

    def _paint(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def ok(self, text: str) -> None:
        print(self._paint("32", "[+] ") + text)

    def info(self, text: str) -> None:
        print(self._paint("36", "[i] ") + text)

    def warn(self, text: str) -> None:
        print(self._paint("33", "[!] ") + text)

    def error(self, text: str) -> None:
        print(self._paint("31", "[-] ") + text, file=sys.stderr)

    def step(self, text: str) -> None:
        print(self._paint("35", "[*] ") + text)
