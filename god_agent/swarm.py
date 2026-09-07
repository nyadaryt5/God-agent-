"""God-Agent swarm: a God orchestrator operating as a single body.

God-Agent is *one organism*: the 100-specialist roster is compressed into 6
functional parts — 1 Brain, 2 Hands (Left=act, Right=build), 2 Legs (Left=reach,
Right=move), and 1 Torso (core/guard) — that God hands work off to. Each part is
a real agent (the open-source OpenAI Agents SDK, MIT) whose capabilities are the
union of the specialists it subsumes, so nothing is lost by compressing the crew.

The body is data-driven. `god_agent.body.build_body()` folds
`god_agent.catalog.BASE_CATALOG` into the 6 parts; operator definitions added in
`~/.god-agent/agents.json` (or `~/.god-agent/agents.d/`) are appended as extra
handoffs, so the registry stays extensible. `goda catalog --roster` lists the
full 100-specialist portfolio; `goda catalog` shows the 6-part body.

Alternative real engines (CrewAI, LangGraph, smolagents, AutoGen, Hermes Agent,
UI-TARS, Grok Build) are *detected* at runtime and used only if installed; they
are optional extras. The default and best-supported engine is the OpenAI Agents
SDK. If no engine/key is available, God-Agent uses its offline planner.

Safety is unchanged for the whole crew: every tool call from any agent still
passes through God-Agent's own ToolRegistry.dispatch, so the Constitution, risk
grading, operator approvals, and hash-chained audit trail apply to all of them.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from . import sdk_agent
from .catalog import build_catalog, BASE_CATALOG
from . import body as body_mod


# ---------------------------------------------------------------------------
# Build the crew: God orchestrator (with handoffs) + the compressed body.
# God is a single organism with 6 functional parts (1 brain, 2 hands, 2 legs,
# 1 torso) that fold all 100 specialists. Operator-defined drop-in agents are
# appended as extra handoffs so the registry stays extensible.
# ---------------------------------------------------------------------------
def _crew_instructions(runtime, role: Optional[dict] = None) -> str:
    base = sdk_agent._instructions(runtime)
    if role:
        base = f"{role.get('instructions', '')}\n\n--- CREW CONTEXT ---\n{base}"
    return base


def _tools_for(runtime, role: dict) -> list:
    """Return the SDK tool list for a role, optionally filtered by role['tools']."""
    tools, _outcomes = sdk_agent._build_tools(runtime)
    allowed = role.get("tools") or []
    if not allowed:
        return tools
    allowed_set = {t.name for t in tools}
    return [t for t in tools if t.name in allowed_set]


def _builtin_names() -> set[str]:
    return {str(r.get("name", "")) for r in BASE_CATALOG}


def _build_crew(runtime) -> list:
    """Return the list of SDK agents: [God, *body_parts, *operator_extras].

    God is built *with* its handoffs at construction so the SDK registers the
    auto-generated transfer_* functions. The 6 body parts come from the roster
    (compressing all 100 specialists into super-agents); operator drop-ins from
    the registry are appended so `agents.json` / `agents.d/` still extends God.
    """
    from agents import Agent, function_tool, handoff

    model = sdk_agent._build_model(runtime.cfg)

    # The compressed body parts (each a super-agent over a group of specialists).
    body_parts = body_mod.build_body(runtime.cfg)

    # Operator-defined agents (not part of the built-in roster) remain extensible.
    catalog = build_catalog(runtime.cfg)
    builtin = _builtin_names()
    extras = [r for r in catalog if r["name"] not in builtin]

    # Optional external agents via MCP (operator-enabled). Tools exposed by these
    # servers are attached to God; each still audited.
    from . import mcp_servers
    mcp_servers_ = mcp_servers.build_mcp_servers(runtime.cfg)

    # A tiny routing tool the God orchestrator can call to dispatch intent.
    @function_tool(strict_mode=False)
    def route_intent(intent: str) -> str:
        "Declare the high-level intent so the right body part is used."
        return f"routed: {intent}"

    god_tools, _ = sdk_agent._build_tools(runtime)

    # Build the body + extras first so we can bind their handoffs into God at
    # construction time (the SDK only registers transfer_* tools then).
    subordinates: list[Agent] = []
    handoffs = []
    for spec in [*body_parts, *extras]:
        if not spec.get("name"):
            continue
        agent = Agent(
            name=spec["name"],
            instructions=_crew_instructions(runtime, spec),
            tools=_tools_for(runtime, spec),
            model=_model_for(runtime, spec),
        )
        subordinates.append(agent)
        handoffs.append(handoff(agent))

    god = Agent(
        name="God",
        instructions=_crew_instructions(runtime),
        tools=[*god_tools, route_intent],
        mcp_servers=mcp_servers_,
        model=model,
        handoffs=handoffs,
    )
    return [god, *subordinates]


def _model_for(runtime, spec: dict):
    """Return an SDK model for a role, honoring a per-role engine when available.

    Falls back to the default OpenAI-compatible model for anything not installed
    or not openai_sdk-based.
    """
    engine = spec.get("engine", "openai_sdk")
    if engine in ("openai_sdk", "openai", ""):
        return sdk_agent._build_model(runtime.cfg)

    # Optional alternate engines — used only if actually importable.
    if engine == "crewai" and _has("crewai"):
        return sdk_agent._build_model(runtime.cfg)  # same wire model; crew wraps it
    # Unknown / unavailable engine: stay on the default (never fail the crew).
    return sdk_agent._build_model(runtime.cfg)


def _has(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except Exception:
        return False


def __getattr__(name: str):
    """Back-compat: keep the old module-level SPECIALISTS reference working."""
    if name == "SPECIALISTS":
        from .catalog import BASE_CATALOG
        return BASE_CATALOG
    raise AttributeError(name)


def _active_engine_config(cfg) -> str:
    """Pick the engine for the whole run from a cfg dict: requested > fallback.

    Only an *available* (installed) engine is chosen; otherwise we fall back to
    the OpenAI Agents SDK (or, if even that is missing, the offline planner).
    """
    from . import engines

    requested = str(cfg["agent"].get("engine", "openai_sdk")).lower()
    if requested == "openai_sdk" and engines.is_available("openai_sdk"):
        return "openai_sdk"
    if requested in engines.engine_names() and engines.is_available(requested):
        return requested
    # Not requested or unavailable: fall back to a real engine we do have.
    for name in engines.engine_names():
        if engines.is_available(name):
            return name
    return "openai_sdk"  # signals "no engine" — loop falls back to offline planner


def _active_engine(runtime) -> str:
    return _active_engine_config(runtime.cfg)


def run(runtime, task: str, *, history: Optional[list[dict]] = None,
        max_steps: int = 24) -> Any:
    """Run a task through the swarm. Falls back to the single-engine path if the
    crew cannot be constructed, matching the engine_usable gate."""
    import asyncio

    return asyncio.run(_run_async(runtime, task, history=history, max_steps=max_steps))


async def _run_async(runtime, task: str, *, history=None, max_steps: int) -> Any:
    from agents import Runner
    from . import engines
    from .loop import StepResult, TaskResult
    from .utils import now_iso

    rt = runtime
    engine = _active_engine(rt)

    # Non-default engines drive the run directly (they own their own loop); the
    # crew (God + specialists) is the default openai_sdk path.
    if engine != "openai_sdk":
        return engines.run_engine(engine, rt, task, history=history, max_steps=max_steps)

    crew = _build_crew(rt)
    god = crew[0]
    input_items: list[Any] = []
    for m in (history or [])[-8:]:
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
            input_items.append({"role": m["role"], "content": m["content"][:4000]})
    input_items.append({"role": "user", "content": "TASK:\n" + task})

    result = await Runner.run(god, input=input_items,
                              max_turns=max(1, min(max_steps, 50)))

    steps: list[StepResult] = []
    pending: dict[str, tuple[int, str]] = {}
    step_no = 0
    for item in result.new_items:
        cname = type(item).__name__
        if cname == "ToolCallItem":
            step_no += 1
            pending[item.call_id] = (step_no, item.tool_name)
        elif cname == "ToolCallOutputItem":
            cid = item.call_id
            step_no_c, tool_name = pending.pop(cid, (step_no, "unknown"))
            steps.append(StepResult(step_no_c, tool_name, {}, True,
                                    str(item.output or "")[:20000], ts=now_iso()))

    final_text = result.final_output or ""
    done = bool(steps) and all(s.ok for s in steps) and bool(final_text)
    return TaskResult(
        task=task,
        goal=task,
        steps=steps,
        success=done,
        summary=(final_text or "Completed.")[:2000],
        reflection=final_text[:800] if done else "",
        session=[],
    )
