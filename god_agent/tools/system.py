"""System tools: inspect host, manage services, install packages, and full
native control: processes, sysctl, tuning, scheduling, GPUs."""
from __future__ import annotations

import json
import os
import re
import shutil
from typing import Any

from ..runtime import get_runtime


def system_info(reg, name: str, args: dict) -> str:
    """Collect host inventory: OS, kernel, CPU, memory, disk, load, top processes, GPUs."""
    rt = get_runtime()
    res = rt.executor.run(
        "uname -a; echo '---'; "
        "cat /etc/os-release 2>/dev/null | head -5; echo '---'; "
        "nproc; echo '---'; free -h 2>/dev/null || cat /proc/meminfo | head -3; echo '---'; "
        "df -h 2>/dev/null | head -12; echo '---'; uptime; echo '---'; "
        "ps aux --sort=-%mem 2>/dev/null | head -8",
        timeout=30,
    )
    gpu = _gpu_summary()
    out = (res.get("stdout") or "") + (("\n[stderr]\n" + res["stderr"]) if res.get("stderr") else "")
    return out + (("\n=== GPU ===\n" + gpu) if gpu else "")


def _gpu_summary() -> str:
    """Best-effort GPU inventory across vendors (no output when none present)."""
    parts = []
    if shutil.which("nvidia-smi"):
        r = run_local("nvidia-smi --query-gpu=name,memory.total,utilization.gpu "
                      "--format=csv,noheader,nounits 2>/dev/null | head -16")
        if r.get("stdout", "").strip():
            parts.append("[nvidia-smi]\n" + r["stdout"])
    if shutil.which("rocm-smi"):
        r = run_local("rocm-smi --showproductname --showuse --showmeminfo vram 2>/dev/null | head -30")
        if r.get("stdout", "").strip():
            parts.append("[rocm-smi]\n" + r["stdout"])
    dri = "/sys/class/drm"
    if os.path.isdir(dri):
        cards = sorted(os.listdir(dri))
        if cards:
            parts.append("[drm devices]\n" + "\n".join(cards))
    return "\n".join(parts)


def run_local(cmd: str, timeout: int = 20) -> dict:
    """Helper: execute through the runtime executor."""
    return get_runtime().executor.run(cmd, timeout=timeout)


def hardware_info(reg, name: str, args: dict) -> str:
    """Full hardware inventory: CPU model/cores/threads, RAM, GPU, disks, PCI, sensors."""
    rt = get_runtime()
    res = rt.executor.run(
        "echo '== CPU =='; lscpu 2>/dev/null | grep -E 'Model name|Architecture|^CPU\\(s\\)|Thread|Core|Socket' "
        "|| grep -E 'model name|processor' /proc/cpuinfo | head -8; "
        "echo '== MEMORY =='; free -b; cat /proc/meminfo | grep -E 'MemTotal|MemAvailable|SwapTotal'; "
        "echo '== DISKS =='; lsblk -o NAME,SIZE,TYPE,MODEL 2>/dev/null | head -30 || fdisk -l 2>/dev/null | head -20; "
        "echo '== PCI =='; lspci 2>/dev/null | head -20; "
        "echo '== SENSORS =='; sensors 2>/dev/null | head -25 || true",
        timeout=40,
    )
    gpu = _gpu_summary()
    out = (res.get("stdout") or "") + (("\n[stderr]\n" + res["stderr"]) if res.get("stderr") else "")
    return out + (("\n=== GPU ===\n" + gpu) if gpu else "")


def gpu_info(reg, name: str, args: dict) -> str:
    """Live GPU status: model, memory, utilization, temperature, processes."""
    rt = get_runtime()
    parts = []
    if shutil.which("nvidia-smi"):
        r = rt.executor.run("nvidia-smi 2>/dev/null | head -40", timeout=30)
        if r.get("stdout", "").strip():
            parts.append("[nvidia-smi]\n" + r["stdout"])
    if shutil.which("rocm-smi"):
        r = rt.executor.run("rocm-smi 2>/dev/null | head -40", timeout=30)
        if r.get("stdout", "").strip():
            parts.append("[rocm-smi]\n" + r["stdout"])
    if not parts:
        return "no GPU detected on this host"
    return "\n".join(parts)


def process_control(reg, name: str, args: dict) -> str:
    """Native process control: list/kill/nice/renice/priority/cgroup/affinity.

    actions: list | kill | nice | renice | affinity
    target:  pid (int) or pattern (substring match on command line)
    """
    rt = get_runtime()
    action = str(args.get("action", "list"))
    target = args.get("target")
    value = args.get("value")

    if action == "list":
        cmd = ("ps -eo pid,ppid,user,%cpu,%mem,stat,ni,cmd --sort=-%cpu | head -60")
        return rt.executor.run(cmd, timeout=20).get("stdout", "")

    if target is None or target == "":
        return "ERROR: target (pid or process-name pattern) required"

    if str(target).isdigit():
        cmd = f"kill -{int(value or 'SIGTERM')} {target}" if action == "kill" else \
              f"renice {value} -p {target}" if action in ("nice", "renice") else \
              f"taskset -pc {value} {target}" if action == "affinity" else \
              f"echo 'unsupported: {action}'"
    else:
        pattern = re.escape(str(target))
        when = f"kill -{int(value or 'SIGTERM')}" if action == "kill" else \
               f"renice {value}" if action in ("nice", "renice") else \
               f"taskset -pc {value}" if action == "affinity" else "echo 'unsupported'"
        cmd = (f"pgrep -af '{pattern}' | head -50; "
               f"echo '---'; for p in $(pgrep -f '{pattern}' | head -200); do "
               f"case $p in $$|$PPID) ;; *) {when} $p 2>&1 | head -1 ;; esac; done")
    res = rt.executor.run(cmd, timeout=30)
    rt.record("process_control", f"{action} on {target}", {"action": action, "target": target, "value": value})
    return (res.get("stdout") or "") + (("\n[stderr]\n" + res["stderr"][-800:]) if res.get("stderr") else "")


def sysctl_set(reg, name: str, args: dict) -> str:
    """Apply kernel parameters natively (e.g. vm.swappiness, net.core.rmem_max).

    args: {"key": "vm.swappiness", "value": "10", "persist": false}
    persist=true writes /etc/sysctl.d/99-goda.conf
    """
    rt = get_runtime()
    key = str(args.get("key", ""))
    value = str(args.get("value", ""))
    persist = bool(args.get("persist", False))
    if not key or not value:
        return "ERROR: key and value required"
    res = rt.executor.run(f"sysctl -w {key}={value}", timeout=20)
    out = (res.get("stdout") or "") + (("\n[stderr]\n" + res["stderr"][-500:]) if res.get("stderr") else "")
    if res.get("exit_code") == 0 and persist:
        p = rt.executor.run(f"mkdir -p /etc/sysctl.d && echo '{key} = {value}' >> "
                            "/etc/sysctl.d/99-goda.conf && sysctl --system >/dev/null 2>&1", timeout=30)
        out += f"\n[persist: exit {p.get('exit_code')}]"
    rt.record("sysctl_set", f"{key}={value}", {"key": key, "value": value, "persist": persist})
    return out


def system_tune(reg, name: str, args: dict) -> str:
    """Tune CPU governor / IO scheduler / THP for max performance.

    args: {"governor": "performance"|"powersave"|"schedutil",
           "io_scheduler": "none"|"mq-deadline"|"bfq",
           "transparent_hugepage": "always"|"never"|"madvise"}
    """
    rt = get_runtime()
    parts = []
    governor = args.get("governor")
    if governor:
        r = rt.executor.run(
            "c=0; for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do "
            f"echo '{governor}' > \"$g\" 2>/dev/null && c=$((c+1)); done; "
            "echo \"applied to $c cpu(s)\"", timeout=20)
        parts.append(f"governor={governor}: {r.get('stdout','').strip()} (exit {r.get('exit_code')})")
    io_sched = args.get("io_scheduler")
    if io_sched:
        r = rt.executor.run(
            "c=0; for d in /sys/block/*/queue/scheduler; do "
            f"echo '{io_sched}' > \"$d\" 2>/dev/null && c=$((c+1)); done; "
            "echo \"applied to $c device(s)\"", timeout=20)
        parts.append(f"io_scheduler={io_sched}: {r.get('stdout','').strip()} (exit {r.get('exit_code')})")
    thp = args.get("transparent_hugepage")
    if thp:
        r = rt.executor.run(
            f"echo {thp} > /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null && "
            "echo applied || echo 'not supported here'", timeout=20)
        parts.append(f"thp={thp}: {r.get('stdout','').strip()} (exit {r.get('exit_code')})")
    rt.record("system_tune", json.dumps(args), dict(args))
    return "\n".join(parts) or "ERROR: provide governor, io_scheduler, or transparent_hugepage"


def schedule_job(reg, name: str, args: dict) -> str:
    """Manage cron jobs natively: add/remove/list a scheduled task.

    args: {"action": "add"|"remove"|"list",
           "schedule": "0 3 * * *", "command": "backup.sh",
           "comment": "nightly backup"}
    """
    rt = get_runtime()
    action = args.get("action", "list")
    if action == "list":
        return rt.executor.run("crontab -l 2>/dev/null || echo '(empty)'", timeout=20).get("stdout", "")
    schedule = str(args.get("schedule", ""))
    command = str(args.get("command", ""))
    comment = str(args.get("comment", ""))
    # Basic sanity: 5 time fields
    if not re.match(r"^(\S+\s+){4}\S+$", schedule.strip()):
        return "ERROR: schedule must be 5 cron fields (e.g. '0 3 * * *')"
    if not command:
        return "ERROR: command required"
    line = (f"# goda:{comment}\n{schedule} {command}")
    if action == "add":
        script = (
            f"tmp=$(mktemp); (crontab -l 2>/dev/null | grep -v '^# goda:{re.escape(comment)}$' "
            f"|| true) > \"$tmp\"; echo {json.dumps(line)} >> \"$tmp\"; crontab \"$tmp\" && echo scheduled"
        )
    elif action == "remove":
        script = (
            f"tmp=$(mktemp); (crontab -l 2>/dev/null | grep -v '^# goda:{re.escape(comment)}$' "
            f"| grep -v '{re.escape(command)}' || true) > \"$tmp\"; crontab \"$tmp\" && echo removed"
        )
    else:
        return "ERROR: action must be add|remove|list"
    res = rt.executor.run(script, timeout=30)
    rt.record("schedule_job", f"{action} cron", {"action": action, "schedule": schedule,
                                                 "command": command, "comment": comment})
    return (res.get("stdout") or res.get("stderr") or "") + f"\n[exit: {res.get('exit_code')}]"


def service_action(reg, name: str, args: dict) -> str:
    """Start/stop/restart/status a systemd service (policy-gated)."""
    rt = get_runtime()
    action = str(args.get("action", "status"))
    service = str(args.get("service", ""))
    if action not in ("status", "start", "stop", "restart", "reload", "enable", "disable"):
        return "ERROR: action must be one of status/start/stop/restart/reload/enable/disable"
    if not service:
        return "ERROR: service name required"
    if shutil.which("systemctl"):
        cmd = f"systemctl {action} {service}"
    else:
        cmd = f"service {service} {action}"
    res = rt.executor.run(cmd, timeout=60)
    rt.record("service_action", f"{action} {service}", {"action": action, "service": service,
                                                        "exit": res.get("exit_code")})
    return (res.get("stdout") or res.get("stderr") or "") + f"\n[exit: {res.get('exit_code')}]"


def package_install(reg, name: str, args: dict) -> str:
    """Install packages with the distro package manager (policy-gated)."""
    rt = get_runtime()
    packages = args.get("packages", [])
    if isinstance(packages, str):
        packages = [packages]
    if not packages:
        return "ERROR: packages list required"
    pkgs = " ".join(str(p) for p in packages)
    if shutil.which("apt-get"):
        cmd = f"DEBIAN_FRONTEND=noninteractive apt-get update -qq && apt-get install -y --no-install-recommends {pkgs}"
    elif shutil.which("dnf"):
        cmd = f"dnf install -y {pkgs}"
    elif shutil.which("yum"):
        cmd = f"yum install -y {pkgs}"
    elif shutil.which("apk"):
        cmd = f"apk add {pkgs}"
    elif shutil.which("pacman"):
        cmd = f"pacman -Sy --noconfirm {pkgs}"
    else:
        return "ERROR: no supported package manager found"
    res = rt.executor.run(cmd, timeout=600)
    rt.record("package_install", f"install {pkgs}", {"packages": pkgs, "exit": res.get("exit_code")})
    tail = (res.get("stdout") or "")[-2000:] + (("\n[stderr]\n" + res["stderr"][-2000:]) if res.get("stderr") else "")
    return (tail or "(no output)") + f"\n[exit: {res.get('exit_code')}]"
