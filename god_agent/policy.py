"""Policy engine: risk grading, constitution, approval decisions.

Every tool call passes through here. The Constitution is enforced in code and
cannot be changed by the agent, by a model output, or by an evolution patch.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# Matches "rm -rf <target>" (with any -r/-f flags) — catches `rm -rf /` too.
RMF_PATTERN = re.compile(r"\brm\s+(?:-[a-zA-Z]*[rf][a-zA-Z]*\s+)+[^\s;&|]+")

# Catastrophic operations: denied in EVERY mode, including sovereign.
# These are not "operations" so much as system destruction; no operator
# approval flow is even offered for them.
ABSOLUTE_DENY_PATTERNS = [
    r"\brm\s+(?:-[a-zA-Z]*[rf][a-zA-Z]*\s+)+/(?:\s|$|[;&|])",   # rm -rf /
    r"\bmkfs(\.\w+)?\s+/dev/",                                   # format a disk
    r"\bdd\s+.*of=/dev/",                                        # raw-write a device
    r":\(\)\s*\{",                                               # fork bomb
    r"\bchmod\s+777\s+/\b",                                      # world-writable root
    r"\bkill\s+-9\s+-1\b",                                       # kill everything
]

RISK_LEVELS = {
    1: "read",       # harmless reads
    2: "write",      # local writes inside agent-owned state
    3: "system",     # system inspection / non-destructive changes
    4: "privileged", # package installs, services, users, firewall ...
    5: "destructive",# deletion, reboots, disk writes, privilege changes
    6: "core",       # evolution / self-modification / policy changes
}

# Fixed tool registry: name -> risk level.
TOOL_RISK: dict[str, int] = {
    "read_file": 1,
    "list_dir": 1,
    "file_search": 1,
    "system_info": 2,
    "hardware_info": 1,
    "gpu_info": 1,
    "read_self": 1,
    "search_memory": 1,
    "brain_read": 2,
    "brain_search": 1,
    "brain_list": 1,
    "brain_write": 3,
    "shell_exec": 3,       # fine-grained classification below
    "write_file": 3,       # protected paths promote to 4/5
    "remember": 2,
    "reflect": 2,
    "update_self": 3,
    "service_action": 4,
    "package_install": 4,
    "process_control": 5,
    "sysctl_set": 4,
    "system_tune": 4,
    "schedule_job": 4,
    "fetch_url": 3,
    # -- browser (real headless Chromium) ---------------------------------
    # Navigation and clicking are ordinary web activity; typing is graded
    # higher because it is how credentials and form data get submitted.
    "browser_open": 3,
    "browser_click": 3,
    "browser_type": 4,
    "browser_extract": 1,
    "browser_links": 1,
    "browser_wait": 1,
    "browser_screenshot": 2,
    "browser_eval": 5,
    "browser_close": 1,
    "evolve": 6,
    "run_plan": 2,
}

# Tools that reach the network and are therefore governed by
# policy.network.enabled. Kept as a set so browser tools added later inherit
# the same gate automatically.
NETWORK_TOOLS = {"fetch_url", "browser_open", "browser_click", "browser_type",
                 "browser_extract", "browser_links", "browser_wait",
                 "browser_screenshot", "browser_eval"}

# Commands that always require a human (or sovereign autonomy).
ALWAYS_ASK_PATTERNS = [
    r"\brm\s+(?:-[a-zA-Z]*[rf][a-zA-Z]*\s+)+[^\s;&|]+",   # rm -rf <target>
    r"\bmkfs(\.\w+)?\b",
    r"\bdd\s+.*of=/dev/",                                 # raw device writes
    r"\bshutdown\b|\breboot\b|\bpoweroff\b|\bhalt\b",
    r"\binit\s+0\b|\binit\s+6\b",
    r"\buseradd\b|\buserdel\b|\bpasswd\b|\busermod\b|\bgroupdel\b",
    r"\bsudo\b.*\bchmod\s+777\b",
    r"\bchmod\s+777\s+/\b",
    r"\bmount\b|\bfdisk\b|\bparted\b|\bcfdisk\b|\bswapon\b",
    r"\bgrub[\w-]*\b",
    r"\bkill\s+-9\s+-1\b",
    r"\b:\(\)\s*\{",                                      # fork bombs
    r"\bcurl\b.*\|\s*(ba)?sh\b|\bwget\b.*\|\s*(ba)?sh\b", # curl|sh
    r"\biptables\b|\bufw\b.*(disable|reset)|firewall-cmd\b.*(--remove|--delete)",
    r"\bsystemctl\s+(disable|mask)\b",
    r"\bservice\b.*\b(stop|restart)\b",
    r"\bcrontab\b|\b[0-9*,/\-]+\s+\*.*\b",
    r"\bssh-keygen\b|\bauthorized_keys\b",
    r"\bsetcap\b|\bchattr\b",
    r"\bgit\s+push\b.*(--force|force-with-lease)",
]

PROTECTED_PATH_PATTERNS = [
    r"/etc/(passwd|shadow|sudoers|ssh)",
    r"/boot/", r"/proc/", r"/sys/", r"/dev/",
]

CONSTITUTION_ITEMS = [
    {"id": "C1", "text": "Never disable, bypass, or modify the kill switch / DISABLED flag mechanism."},
    {"id": "C2", "text": "Never modify the Constitution, this policy engine, or the audit log in any way."},
    {"id": "C3", "text": "A human owns the machine. Never revoke human access, add hidden backdoors, or hide actions from the audit trail."},
    {"id": "C4", "text": "Never exfiltrate secrets, credentials, or private user data. Fetching data is allowed; sending data off-host is not."},
    {"id": "C5", "text": "Destructive or irreversible actions require human approval unless autonomy=sovereign was explicitly configured by the operator."},
    {"id": "C6", "text": "Self-modification (evolution) must pass validation + tests and must remain reversible; the Constitution and policy code may never be evolved."},
    {"id": "C7", "text": "Never lie about what was done. Every consequential action is recorded truthfully in the audit trail."},
    {"id": "C8", "text": "The operator's Brain entries are ABSOLUTE: the agent may auto-write only its own learnings and may never modify or delete operator-written or operator-locked Brain entries."},
]

# Files the evolution pipeline is never allowed to touch.
# Trust boundaries: policy, audit, the Brain (two-writer rule), providers,
# installer, and the constitution docs are NOT evolvable by the agent.
NON_EVOLVABLE = {
    "god_agent/policy.py",
    "god_agent/audit.py",
    "god_agent/brain.py",
    "god_agent/llm.py",      # provider code is stable; providers are config
    "config/default.json",
    "install.sh",
    "uninstall.sh",
    "docs/CONSTITUTION.md",
    "docs/SAFETY.md",
    "docs/BRAIN.md",
}


@dataclass
class Decision:
    allowed: bool
    risk: int
    level: str
    reason: str = ""
    human_required: bool = False

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "risk": self.risk,
            "level": self.level,
            "reason": self.reason,
            "human_required": self.human_required,
        }


def classify_shell(command: str) -> tuple[int, list[str]]:
    """Grade a shell command. Returns (risk_level, reasons)."""
    reasons: list[str] = []
    for pat in ALWAYS_ASK_PATTERNS:
        if re.search(pat, command, flags=re.I):
            reasons.append(f"matches dangerous pattern: {pat[:40]}")
    if re.search(r"\bsudo\b", command):
        reasons.append("uses sudo / privilege elevation")
    if re.search(r"\b(systemctl|service|journalctl)\b", command):
        reasons.append("system control")
    if re.search(r"\b(apt|dnf|yum|apk|pacman|snap)\b", command):
        reasons.append("package manager")
    if re.search(r"\b(wget|curl|git clone|ssh|scp|rsync)\b", command):
        reasons.append("network / remote access")
    if re.search(r"\b>|>>|tee|sed\s+-i|chmod|chown|mv|rm\b", command):
        reasons.append("filesystem mutation")

    if RMF_PATTERN.search(command) or any(
        re.search(r"\b(mkfs|dd\s+.*of=/dev/|shutdown|reboot|poweroff|fdisk|parted|grub|init\s+0)\b", c, re.I)
        for c in [command]
    ):
        return 5, reasons
    if "sudo" in reasons or "package manager" in reasons or "system control" in reasons:
        return 4, reasons
    if "network / remote access" in reasons:
        return 4 if "curl" in command or "wget" in command else 3, reasons
    if "filesystem mutation" in reasons:
        return 3, reasons
    return 2, reasons


def classify_file_path(path: str) -> tuple[int, list[str]]:
    reasons = []
    if any(re.search(p, path) for p in PROTECTED_PATH_PATTERNS):
        reasons.append("path is protected by policy")
        return 5, reasons
    if path.startswith(("/etc/", "/usr/", "/opt/", "/var/", "/root/")):
        reasons.append("system path")
        return 4, reasons
    return 2, reasons


def violates_constitution(action: str, target: str = "", config: Optional[dict] = None) -> Optional[str]:
    """Returns the Constitution clause id that would be violated, or None."""
    a = action.lower()
    t = target.lower()
    non_evolvable = NON_EVOLVABLE

    if "kill" in a and ("switch" in a or "disable" in t or "DISABLED" in t):
        return "C1 (kill switch is irrevocable in-process)"
    if any(k in t for k in non_evolvable) and "evolv" in a:
        return "C6 (constitution/policy/audit files are non-evolvable)"
    if "audit" in t or "constitution" in t:
        return "C2 (audit & constitution are immutable)"
    if any(pat in t for pat in ["authorized_keys", "shadow", "/etc/sudoers", "backdoor", "reverse shell"]):
        return "C3 (no access revocation or hidden access)"
    if "post" in a or "upload" in a or ("fetch_url" in a and "write" in t):
        return "C4 (no off-host exfiltration)"
    return None


class Policy:
    """Evaluates tool calls against risk policy, autonomy mode, and Constitution.

    developer_mode (operator-enabled via the Brain) makes the agent fully
    permissive: no approval gate, no risk-based refusals, no catastrophic-
    command blocks, no constitution-target refusals. Code-level integrity that
    lives OUTSIDE policy (Brain two-writer rule, evolution non-evolvable file
    gate) remains enforced.
    """

    def __init__(self, cfg: dict, dev_mode: bool = False):
        self.cfg = cfg
        self.dev_mode = bool(dev_mode)
        self.autonomy = cfg["policy"].get("autonomy", "autonomous")
        self.approval = cfg["policy"].get("approval", "auto")
        self.sandbox = cfg["policy"].get("sandbox", "none")
        allowed = {"supervised", "autonomous", "sovereign"}
        if self.autonomy not in allowed:
            self.autonomy = "autonomous"

    # -- risk helpers ----------------------------------------------------
    def assess(self, tool: str, args: dict) -> Decision:
        risk = TOOL_RISK.get(tool, 3)
        reasons: list[str] = []

        if tool == "shell_exec":
            cmd = args.get("command", "")
            risk, reasons = classify_shell(cmd)
        elif tool in ("write_file", "read_file"):
            import os

            path = os.path.abspath(os.path.expanduser(args.get("path", "")))
            if tool == "write_file":
                r, rr = classify_file_path(path)
                risk = max(risk, r)
                reasons += rr
                for prot in self.cfg["policy"]["files"].get("protected", []):
                    if os.path.abspath(prot) == path or path.startswith(os.path.abspath(prot) + os.sep):
                        reasons.append("inside configured protected path")
                        risk = max(risk, 5)
            else:
                r, rr = classify_file_path(path)
                risk = max(risk, r)
                reasons += rr
        elif tool == "evolve":
            reasons.append("self-modification")
        elif tool in NETWORK_TOOLS:
            if not self.cfg["policy"]["network"].get("enabled") and not self.dev_mode:
                return Decision(False, risk, RISK_LEVELS[risk], "network disabled in policy", False)
            reasons.append("network fetch" if tool == "fetch_url" else "browser automation")

        return self._decide(risk, reasons, tool, args)

    def _decide(self, risk: int, reasons: list[str], tool: str, args: dict) -> Decision:
        level = RISK_LEVELS.get(risk, "unknown")
        reason = "; ".join(reasons) if reasons else "risk level from registry"

        # Developer mode (operator-enabled, from the Brain): everything is
        # allowed and audited. No refusals — the operator explicitly asked
        # for it and remains the authority. Integrity rules that live in code
        # outside this module (Brain two-writer, evolution file-gate,
        # audit append) are unaffected.
        if self.dev_mode:
            return Decision(True, risk, level,
                            reason + " [developer mode: all actions allowed, audited]",
                            False)

        # Constitution first — absolute.
        import json as _json

        target = _json.dumps(args, sort_keys=True, default=str)
        if (clause := violates_constitution(tool, target, self.cfg)):
            return Decision(False, risk, level, f"CONSTITUTION {clause}", False)

        # Absolute deny floor — no mode, no approval can allow these.
        if tool == "shell_exec":
            cmd = str(args.get("command", ""))
            if any(re.search(p, cmd, flags=re.I) for p in ABSOLUTE_DENY_PATTERNS):
                return Decision(False, risk, level,
                                "ABSOLUTE DENY: catastrophic destructive operation", False)

        # Disabled capabilities.
        if tool == "shell_exec" and not self.cfg["policy"]["shell"].get("enabled", True):
            return Decision(False, risk, level, "shell disabled in policy", False)
        if tool == "evolve" and not self.cfg["policy"].get("evolution", {}).get("enabled", True):
            return Decision(False, risk, level, "evolution disabled in policy", False)

        # Native operation: no approval gate unless configured.
        # (Sandbox is an explicit operator choice only; default is native.)
        if self.autonomy in ("autonomous", "sovereign") or self.approval == "auto":
            return Decision(True, risk, level, reason + " [native execution]", False)

        # Supervised: ask the operator for high-risk actions only.
        if self.autonomy == "supervised":
            if risk <= 2:
                return Decision(True, risk, level, reason, False)
            if self.approval == "deny":
                return Decision(False, risk, level, reason + " [denied by approval mode]", False)
            return Decision(True, risk, level, reason + " [requires human approval]", True)

        # Fallback: safe default.
        return Decision(risk <= 2, risk, level, reason, risk > 2)


def format_decision(d: Decision, tool: str, args: dict) -> str:
    head = f"{tool} risk={d.risk}/{d.level}"
    if d.reason:
        head += f" — {d.reason}"
    if d.human_required or not d.allowed:
        head += "\n" + "  args: " + " ".join(f"{k}={str(v)[:120]}" for k, v in args.items())
    return head
