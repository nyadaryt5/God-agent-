"""The God-Agent body: 100 specialists compressed into one organism.

Instead of a crew of 100 discrete specialist agents that God hands off to, God
now *is* a body — a single agent that operates as one organism with functional
parts. The 100 specialists are folded into **6 super-agents** (1 brain, 2 hands,
2 legs, 1 torso), each a real agent whose capabilities are the union of the
specialists it subsumes. The body is God's visible self-identity: how it
describes itself, how status/self render it, and how it routes work.

The mapping is a pure compression of the *existing* roster — it adds no new
agents and removes none from the portfolio. `god_agent.catalog.BASE_CATALOG`
still lists all 100 specialists (so `goda catalog` retains the full portfolio and
its count); this module groups them into body parts and exposes the 6 agents the
swarm actually runs.

    Body part    = domain(s) it subsumes        count
    -----------  = ---------------------------  -----
    Brain        = intel                          8
    LeftHand     = ops                           16
    RightHand    = dev + web                     24
    LeftLeg      = network                       10
    RightLeg     = data                          12
    Torso        = security + guard + cloud      30
    -----------  ---------------------------  -----
    TOTAL                                         100

Safety is unchanged: every tool call from any body part still flows through
God-Agent's own ToolRegistry.dispatch (Constitution + policy + approvals + audit).
"""
from __future__ import annotations

from typing import Optional

from .catalog import BASE_CATALOG, _domain_tools


# ---------------------------------------------------------------------------
# The six body parts. Each specifies the catalog domains it subsumes; the
# tool set and member list are computed from those domains at build time so the
# body always stays in sync with the roster.
# ---------------------------------------------------------------------------
BODY_PARTS: list[dict] = [
    {
        "name": "Brain",
        "role": "think",
        "domains": ["intel"],
        "handoff": "Reasoning, planning, memory, research, synthesis, self-reflection, ethics review.",
        "instructions": (
            "You are the Brain of the God-Agent body. You are the mind that "
            "thinks, plans, remembers, and reflects. Fold into one super-agent "
            "the specialists of the intel domain: deep research, knowledge "
            "curation, trend analysis, fact-checking, incident postmortems, and "
            "reasoned synthesis. You lead with a plan, break goals into steps, "
            "recall relevant memory/Brain context, and reflect honestly. You do "
            "NOT act on the host — you coordinate and reason, and you advise the "
            "hands, legs, and torso. Never fabricate a result; cite what you know."
        ),
    },
    {
        "name": "LeftHand",
        "role": "act",
        "domains": ["ops"],
        "handoff": "System operations: services, packages, processes, kernel, disk, boot, storage, hardware.",
        "instructions": (
            "You are the Left Hand of the God-Agent body — the operating hand "
            "that acts on the host. Fold into one super-agent the ops "
            "specialists: system administration, performance, disk/storage, "
            "services, packages, processes/kernel, boot recovery, RAID/LVM, "
            "snapshots, hardware inventory, power/thermal, and NFS mounts. You "
            "execute the plan. Prefer the least invasive, reversible operation, "
            "always verify before acting, and never hide an action. You may act "
            "natively, but every action still passes through God's policy gate."
        ),
    },
    {
        "name": "RightHand",
        "role": "build",
        "domains": ["dev", "web"],
        "handoff": "Create/modify: code, configs, web/apps, builds, CI/CD, deploys, docs.",
        "instructions": (
            "You are the Right Hand of the God-Agent body — the building hand "
            "that creates and modifies. Fold into one super-agent the dev and "
            "web specialists: frontend/backend/code, web servers, reverse proxy, "
            "API gateway, cache, message queue, search, CI/CD, tests, releases, "
            "migrations, git, and docs. You author edits that are correct, "
            "reviewed, and reversible. Never introduce code that hides a behavior "
            "from the audit trail or weakens security."
        ),
    },
    {
        "name": "LeftLeg",
        "role": "reach",
        "domains": ["network"],
        "handoff": "Movement/connectivity: DNS, routing, sockets, fetch/reachability, proxy/VPN, load balance, traffic.",
        "instructions": (
            "You are the Left Leg of the God-Agent body — the moving, reaching "
            "part that connects. Fold into one super-agent the network "
            "specialists: connectivity, DNS, routing, sockets, latency, packet "
            "capture, proxy/VPN, load balancing, wireless/LAN, cloud networking, "
            "and monitoring. You diagnose connectivity and fetch external "
            "resources. Network access is governed by policy — never disable it, "
            "test before and after, and never exfiltrate data."
        ),
    },
    {
        "name": "RightLeg",
        "role": "move",
        "domains": ["data"],
        "handoff": "Data movement/storage: databases, pipelines, warehouses, streams, backups, retention.",
        "instructions": (
            "You are the Right Leg of the God-Agent body — the part that moves "
            "and stores data. Fold into one super-agent the data specialists: "
            "databases, data pipelines, warehouses, streaming, NoSQL, SQL "
            "optimization, data quality, governance, privacy, and backups. You "
            "inspect and move data carefully. Never drop or overwrite data "
            "without a plan; guard against destructive SQL and never exfiltrate "
            "credentials or personal data."
        ),
    },
    {
        "name": "Torso",
        "role": "core",
        "domains": ["security", "guard", "cloud"],
        "handoff": "Core container: security posture, identity/access, constitution, audit, cloud/IAM, container security.",
        "instructions": (
            "You are the Torso of the God-Agent body — the core that carries "
            "and protects everything. Fold into one super-agent the security, "
            "guard, and cloud specialists: vulnerability scanning, secrets "
            "vault, pen-testing, compliance, threat intel, malware, encryption, "
            "identity/access, incident response, plus the Constitution Guard, "
            "Audit Guard, Policy Enforcer, Ethics, and Operational Guard, and the "
            "cloud specialists (Kubernetes, IaC, IAM, cost, serverless, "
            "multi-cloud, container security, autoscaling, storage). You hold the "
            "body's boundaries: you enforce the Constitution and policy, verify "
            "the audit chain, and protect against exfiltration. You can advise "
            "and guard, but never override the Constitution or hide an action."
        ),
    },
]


# ---------------------------------------------------------------------------
# Build: turn the roster into the 6 compressed body agents.
# ---------------------------------------------------------------------------
def _member_specs(domains: list[str]) -> list[dict]:
    """The BASE_CATALOG specialists belonging to the given domains."""
    wanted = {d.lower() for d in domains}
    return [r for r in BASE_CATALOG if str(r.get("domain", "")).lower() in wanted]


def _member_names(domains: list[str]) -> list[str]:
    return [str(r.get("name", "")) for r in _member_specs(domains)]


def _part_tools(domains: list[str]) -> list[str]:
    """Union of the domain tool subsets for the given domains (de-duped, ordered)."""
    seen: dict[str, None] = {}
    for d in domains:
        for t in _domain_tools().get(d.lower(), []):
            seen.setdefault(t, None)
    return list(seen)


def build_body(cfg: Optional[dict] = None) -> list[dict]:
    """Return the 6 normalized body-agent role dicts.

    Each part carries the canonical role fields (name/domain/handoff/
    instructions/tools/engine) so it slots into the existing swarm tooling. We
    set ``engine = 'openai_sdk'`` and a ``members`` list so callers can show
    which specialists each part compresses.
    """
    parts: list[dict] = []
    for part in BODY_PARTS:
        domains = [str(d).lower() for d in part.get("domains", [])]
        parts.append({
            "name": part["name"],
            "role": part.get("role", ""),
            "domain": "+".join(domains),   # e.g. "dev+web" or "security+guard+cloud"
            "handoff": part["handoff"],
            "instructions": part["instructions"],
            "tools": _part_tools(domains),
            "engine": "openai_sdk",
            "members": _member_names(domains),
        })
    return parts


def body_identity() -> str:
    """The humanoid self-description used in the self-model identity."""
    return "God-Agent (goda) — a single self-aware AI body: 1 Brain, 2 Hands (Left=act, Right=build), 2 Legs (Left=reach, Right=move), 1 Torso (core/guard)"


def body_summary() -> str:
    parts = build_body()
    lines = [
        f"  • {p['name']:<11} [{p['domain']:<24}] {p['handoff']}"
        for p in parts
    ]
    total = sum(len(p["members"]) for p in parts)
    return "\n".join(lines) + f"\n  — total specialists compressed: {total}"


def coverage() -> dict:
    """Validate that every catalog specialist is folded into exactly one part."""
    parts = build_body()
    members = [m for p in parts for m in p["members"]]
    roster = [str(r.get("name", "")) for r in BASE_CATALOG]
    missing = [n for n in roster if n not in members]          # nothing dropped
    dupes = [n for n in set(members) if members.count(n) > 1]  # nothing doubled
    return {"catalog": len(roster), "folded": len(members),
            "missing": missing, "duplicates": dupes}


def parts() -> list[str]:
    return [p["name"] for p in BODY_PARTS]
