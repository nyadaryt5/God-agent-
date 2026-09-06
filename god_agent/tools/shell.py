"""Shell execution tool."""
from __future__ import annotations

from typing import Any

from ..runtime import get_runtime


def shell_exec(reg, name: str, args: dict) -> str:
    """Run a shell command on the host with time limits. Use for admin work."""
    rt = get_runtime()
    command = str(args.get("command", "")).strip()
    if not command:
        return "ERROR: no command provided"
    timeout = int(args.get("timeout", 0) or 0)
    task_timeout = int(rt.cfg["agent"].get("task_timeout_s", 0) or 0)
    if task_timeout > 0:
        timeout = timeout if timeout > 0 else task_timeout
        timeout = min(timeout, task_timeout)
    else:
        timeout = timeout if timeout > 0 else None  # 0 = no artificial timeout
    cwd = args.get("cwd") or None

    result = rt.executor.run(command, timeout=timeout, cwd=cwd)
    rt.record("shell_exec", f"exit={result.get('exit_code')}", {
        "command": result.get("command", ""), "duration_s": result.get("duration_s"),
    }, outcome="ok" if result.get("exit_code") == 0 else "error")

    out = []
    if result.get("stdout"):
        out.append("[stdout]\n" + result["stdout"])
    if result.get("stderr"):
        out.append("[stderr]\n" + result["stderr"])
    out.append(f"[exit code: {result.get('exit_code')} | {result.get('duration_s')}s]")
    return "\n".join(out)[: rt.cfg["policy"]["shell"].get("max_output_chars", 50_000)]
