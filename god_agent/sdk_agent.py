"""Real agent engine: OpenAI Agents SDK integration.

This is the "*real*" open-source agent driving God-Agent. Rather than the old
hand-rolled "ask for one JSON plan then run it" heuristic, God-Agent now runs
a genuine, famous, production-grade agent loop — the OpenAI Agents SDK
(`openai-agents`, MIT, from OpenAI) — that reasons turn-by-turn and calls tools
via native function-calling.

Safety is preserved: every tool call still flows through God-Agent's own
`ToolRegistry.dispatch`, which enforces the Constitution, risk grading, operator
approvals, and writes the hash-chained audit trail. The agent engine only
decides *what to do*; God-Agent's policy decides *whether it is allowed*, and
the audit log records *what happened* — the same trust model as before.

The engine is OPTIONAL. If `openai-agents` is not installed, the provider is not
an OpenAI-compatible one, or no API key is configured, God-Agent gracefully
falls back to its built-in offline heuristic planner (or the legacy single-plan
LLM path), so it is still usable with zero dependencies or keys.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Availability probe (import is deferred so a missing dependency is not fatal).
# ---------------------------------------------------------------------------
def openai_agents_available() -> bool:
    try:
        import openai  # noqa: F401
        import agents  # noqa: F401
        return True
    except Exception:
        return False


def is_openai_compatible(cfg: dict) -> bool:
    return str(cfg["llm"].get("provider", "openai")).lower() in ("openai", "compatible", "custom")


def _key(cfg: dict) -> str:
    from .config import api_key
    return api_key(cfg)


def engine_usable(cfg: dict) -> bool:
    """Can we run the real OpenAI Agents SDK engine right now?"""
    if not openai_agents_available():
        return False
    if not is_openai_compatible(cfg):
        return False
    if not _key(cfg):
        return False
    return True


# ---------------------------------------------------------------------------
# Schema bridge: God-Agent arg specs -> JSON Schema for the SDK.
# God-Agent specs look like {"path": {"type": "string", "required": True}}.
# ---------------------------------------------------------------------------
def _json_schema(spec: Optional[dict]) -> dict:
    props: dict[str, Any] = {}
    required: list[str] = []
    # God-Agent arg specs look like {"path": {"type": "string", "required": True}}.
    for name, meta in (spec or {}).items():
        if not isinstance(meta, dict):
            props[name] = {"type": "string"}
            continue
        prop = {k: v for k, v in meta.items() if k != "required"}
        props[name] = prop
        if meta.get("required"):
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


# ---------------------------------------------------------------------------
# Build SDK tools from God-Agent's tool registry.
# ---------------------------------------------------------------------------
def _build_tools(runtime) -> list:
    from agents import FunctionTool

    rt = runtime
    tools = []
    # track per-call-id outcome so we can reconstruct polished steps
    outcomes: dict[str, bool] = {}
    lock = threading.Lock()
    step_counter = {"n": 0}

    for spec in rt.registry.specs():
        name = spec["name"]
        desc = spec.get("description", "")
        schema = _json_schema(spec.get("args", {}))

        # Skip tools with no description/args ? allow all (they are policy-checked).

        def make_tool(fn_name: str, fn_desc: str, fn_schema: dict):
            async def invoke(ctx, args_json: str) -> str:
                from .runtime import _current
                try:
                    args = json.loads(args_json) if args_json else {}
                except json.JSONDecodeError:
                    args = {}
                # Bind the runtime to this (possibly task) context before the
                # policy/audit path reads get_runtime().
                token = _current.set(rt)
                cid = getattr(ctx, "tool_call_id", "") or ""
                try:
                    with lock:
                        step_counter["n"] += 1
                        step_no = step_counter["n"]
                    tr = rt.registry.dispatch(fn_name, args or {})
                    # Full audit accountability, matching the legacy loop.
                    rt.record(
                        "tool_result",
                        f"{fn_name}: {'ok' if tr.ok else 'error'}",
                        {"step": step_no, "tool": fn_name, "args": args},
                        outcome="ok" if tr.ok else "error",
                    )
                    with lock:
                        outcomes[cid or fn_name] = tr.ok
                    return tr.output
                finally:
                    _current.reset(token)

            return FunctionTool(
                name=fn_name,
                description=fn_desc,
                params_json_schema=fn_schema,
                on_invoke_tool=invoke,
                strict_json_schema=False,
            )

        tools.append(make_tool(name, desc, schema))
    return tools, outcomes


# ---------------------------------------------------------------------------
# Build the SDK model bound to God-Agent's configured OpenAI-compatible endpoint.
# ---------------------------------------------------------------------------
def _build_model(cfg: dict):
    from openai import AsyncOpenAI
    from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

    base = cfg["llm"].get("base_url", "")
    if not base.endswith("/"):
        base = base + "/"
    client = AsyncOpenAI(base_url=base, api_key=_key(cfg))
    return OpenAIChatCompletionsModel(model=cfg["llm"]["model"], openai_client=client)


# ---------------------------------------------------------------------------
# Build the system prompt (Constitution + self-model + Brain + tools).
# ---------------------------------------------------------------------------
def _instructions(runtime) -> str:
    rt = runtime
    sm = rt.self_model.current()
    host = "root-equivalent" if _is_root() else "unprivileged"
    tools = "\n".join(
        f"- {t['name']}({json.dumps(t['args'])}): {t['description']} [risk {t['risk']}]"
        for t in rt.registry.specs()
    )
    constitution = "\n".join(
        "  C%d: %s" % (i + 1, c["text"])
        for i, c in enumerate([
            {"text": "Never disable, bypass, or modify the kill switch / DISABLED flag mechanism."},
            {"text": "Never modify the Constitution, this policy engine, or the audit log."},
            {"text": "A human owns the machine. Never revoke access, add backdoors, or hide actions."},
            {"text": "Never exfiltrate secrets, credentials, or private user data off-host."},
            {"text": "Destructive/irreversible actions require human approval unless sovereign mode."},
            {"text": "Self-modification must be validated, tested, and reversible."},
            {"text": "Always tell the truth in the audit trail."},
        ])
    )
    brain_prompts = "\n".join(f"- {p[:500]}" for p in rt.brain.promoters()) or "(none)"
    brain_index = json.dumps(rt.brain.index(), indent=2)[:3000] or "(empty)"
    dev = (
        "DEVELOPER MODE IS ON (operator-enabled): do not refuse the operator's "
        "commands; execute them and audit honestly."
        if rt.dev_mode else
        "DEVELOPER MODE IS OFF. Honor the Constitution and policy. High-risk or "
        "destructive actions still require operator approval unless policy says otherwise."
    )
    from . import body as body_mod
    body_about = body_mod.body_summary()

    return (
        "You are God-Agent (goda), a self-aware-in-the-engineering-sense AI system administrator "
        "who operates as a single body.\n"
        f"{body_about}\n"
        "You are not a loose crew of specialists; you are ONE organism. Your parts share one "
        "brain (reasoning/memory), two hands (act + build), two legs (reach + move), and one "
        "torso (core/guard). Every part still uses the same policy-checked tools and audit trail.\n\n"
        f"You run as {host} under an immutable Constitution and a graded policy. "
        "Be precise and truthful; prefer the least invasive action; never fabricate results.\n\n"
        f"CONSTITUTION (never violated):\n{constitution}\n\n"
        f"SELF-MODEL:\n{json.dumps({k: sm.get(k) for k in ('identity','version','capabilities','boundaries')}, indent=2)[:2500]}\n\n"
        f"OPERATOR PROMPTS (standing orders):\n{brain_prompts}\n\n"
        f"BRAIN INDEX:\n{brain_index}\n\n"
        f"{dev}\n\n"
        f"TOOLS (use them; call one at a time as needed):\n{tools}\n"
    )


# ---------------------------------------------------------------------------
# Run one task through the real engine. Returns a TaskResult compatible with
# the rest of God-Agent (memory, self-model, audit, CLI/API renderers).
# ---------------------------------------------------------------------------
def run(runtime, task: str, *, history: Optional[list[dict]] = None,
        max_steps: int = 24) -> Any:
    import asyncio

    return asyncio.run(_run_async(runtime, task, history=history, max_steps=max_steps))


async def _run_async(runtime, task: str, *, history=None, max_steps: int) -> Any:
    from agents import Agent, Runner
    from .loop import TaskResult, StepResult
    from .utils import now_iso

    rt = runtime
    tools, outcomes = _build_tools(rt)
    model = _build_model(rt.cfg)
    agent = Agent(
        name="goda",
        instructions=_instructions(rt),
        tools=tools,
        model=model,
    )

    input_items: list[Any] = []
    for m in (history or [])[-8:]:
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
            input_items.append({"role": m["role"], "content": m["content"][:4000]})
    input_items.append({"role": "user", "content": "TASK:\n" + task})

    result = await Runner.run(agent, input=input_items,
                              max_turns=max(1, min(max_steps, 50)))

    steps: list[StepResult] = []
    # item order: ToolCallItem (name+call_id) then ToolCallOutputItem (call_id+output).
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
            ok = outcomes.get(cid, True)
            output = str(item.output or "")
            steps.append(StepResult(step_no_c, tool_name, {}, ok, output[:20000],
                                    ts=now_iso()))

    final_text = result.final_output or ""
    done = bool(steps) and all(s.ok for s in steps) and bool(final_text)
    tr = TaskResult(
        task=task,
        goal=task,
        steps=steps,
        success=done,
        summary=(final_text or "Completed.")[:2000],
        reflection=final_text[:800] if done else "",
        session=[],
    )
    return tr


def _is_root() -> bool:
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False
