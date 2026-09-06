"""Command execution — native and unsandboxed by default.

The executive never runs anything without a Policy decision first; policy
lives in policy.py. Resource limits are configurable under `execution` and
default to NO limits (-1) so the agent can use every bit of RAM/CPU/GPU.
"""
from __future__ import annotations

import os
import shlex
import time
from typing import Callable, Optional

from .utils import redact, run_command, which

Approver = Callable[[str, dict], bool]


class Executor:
    def __init__(self, cfg: dict, approver: Optional[Approver] = None):
        self.cfg = cfg
        self.approver = approver
        self.sandbox = cfg["policy"].get("sandbox", "none")
        ex = cfg.get("execution", {})
        # -1 / 0 → unlimited (native). Positive → impose that limit.
        self.memory_limit_mb = int(ex.get("memory_limit_mb", -1) or -1)
        self.cpu_limit_s = int(ex.get("cpu_limit_s", -1) or -1)
        self.max_processes = int(ex.get("max_processes", -1) or -1)
        self.kill_switch_interval_s = int(ex.get("kill_switch_interval_s", 2) or 2)

    # ------------------------------------------------------------------
    def run(self, command: str, *, timeout: Optional[int] = None,
            cwd: Optional[str] = None, details: Optional[dict] = None) -> dict:
        """Execute a shell string through /bin/sh natively."""
        details = details or {}
        shell_cfg = self.cfg["policy"]["shell"]
        if self.sandbox == "docker" and which("docker"):
            result = self._run_docker(command, timeout, cwd)
        else:
            result = self._run_local(command, timeout, cwd)

        max_out = shell_cfg.get("max_output_chars", 500_000)
        result["stdout"] = redact(result.get("stdout", ""))[-max_out:]
        result["stderr"] = redact(result.get("stderr", ""))[-max_out:]
        result["command"] = redact(command)[:2000]
        result["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return result

    # ------------------------------------------------------------------
    def _run_local(self, command: str, timeout: Optional[int], cwd: Optional[str]) -> dict:
        if timeout is not None and timeout <= 0:
            timeout = None  # 0/negative = no artificial timeout (native)
        return run_command(
            ["/bin/sh", "-c", command],
            timeout=timeout,
            cwd=cwd,
            memory_limit_mb=None if self.memory_limit_mb <= 0 else self.memory_limit_mb,
            cpu_limit_s=None if self.cpu_limit_s <= 0 else self.cpu_limit_s,
            nproc=None if self.max_processes <= 0 else self.max_processes,
        )

    def _run_docker(self, command: str, timeout: Optional[int], cwd: Optional[str]) -> dict:
        volume = ""
        if cwd and os.path.isdir(cwd):
            volume = f"-v {shlex.quote(os.path.abspath(cwd))}:/work -w /work"
        cmd = (
            "docker run --rm --network none --memory 512m --memory-swap 512m "
            "--pids-limit 64 --cpus 1 --read-only --tmpfs /tmp "
            f"{volume} --entrypoint /bin/sh docker.io/library/bash:5 -lc {shlex.quote(command)}"
        )
        try:
            result = run_command(cmd.split(), timeout=(timeout or 300) + 30)
        except Exception as e:  # docker usually returns non-zero, but be safe
            return {"exit_code": 1, "stdout": "", "stderr": f"sandbox error: {e}",
                    "duration_s": 0.0}
        return result

    # ------------------------------------------------------------------
    @staticmethod
    def temp_dir() -> str:
        import tempfile

        return tempfile.mkdtemp(prefix="goda-")
