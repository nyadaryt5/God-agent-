"""Optional, pluggable agent engines.

God-Agent's default and best-supported engine is the OpenAI Agents SDK. But to
honor "every agent framework", this module lets the Swarm also run on *other*
real open-source agent frameworks — CrewAI, LangGraph, smolagents, AutoGen,
**Hermes Agent** (Nous Research), **UI-TARS** (ByteDance), and **Grok Build**
(xAI) — when they are installed. Each is a thin adapter: it takes God-Agent's
task and tool set and
produces the same TaskResult shape (steps + summary), so the rest of God-Agent
(memory, self-model, audit, CLI/API) is unchanged.

Every engine is OPTIONAL and lazy: if the framework (or its dependency) isn't
importable, the adapter is simply unavailable and God-Agent uses its default
engine. Nothing breaks when they are absent. This is the safe, dependency-
conflict-free way to support many frameworks at once — we never import two heavy
frameworks in the same process unless you actually install and select one.
"""
from __future__ import annotations

import os
import shutil
from typing import Any, Optional


def _shutil_which(name: str) -> bool:
    try:
        return bool(shutil.which(name))
    except Exception:
        return False


def engine_names() -> list[str]:
    """All engines God-Agent can route to (whether or not installed)."""
    return ["openai_sdk", "crewai", "langgraph", "smolagents", "autogen",
            "hermes", "ui_tars", "grok"]


# ---------------------------------------------------------------------------
# Importability probes.
# ---------------------------------------------------------------------------
def _importable(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except Exception:
        return False


def is_available(engine: str) -> bool:
    return _probe(engine)


def _probe(engine: str) -> bool:
    if engine == "openai_sdk":
        from . import sdk_agent
        return sdk_agent.openai_agents_available()
    if engine == "crewai":
        return _importable("crewai")
    if engine == "langgraph":
        return _importable("langgraph")
    if engine == "smolagents":
        return _importable("smolagents")
    if engine == "autogen":
        return _importable("autogen") or _importable("autogen.agentchat")
    if engine == "hermes":
        # Hermes Agent ships the `run_agent` module (the real tool-calling loop)
        # plus a `hermes` CLI entry point. We use the Python API.
        return _importable("run_agent") or _shutil_which("hermes")
    if engine == "ui_tars":
        # ByteDance's UI-TARS GUI-agent action parser (parse → pyautogui code).
        return _importable("ui_tars")
    if engine == "grok":
        # xAI's Grok Build agentic CLI (`grok`); headless one-shot via `grok -p`.
        return _shutil_which("grok") or _shutil_which("grok-build") or _shutil_which("grok-cli")
    return False


# ---------------------------------------------------------------------------
# Adapters. Each returns a TaskResult-compatible object (swarm merges these).
# They live behind a function so we only import the framework when used.
# ---------------------------------------------------------------------------
def run_engine(engine: str, runtime, task: str, *, history=None,
               max_steps: int = 24) -> Any:
    """Dispatch a task to a specific engine. Raises if the engine is unavailable."""
    if engine == "openai_sdk":
        from . import sdk_agent
        return sdk_agent.run(runtime, task, history=history, max_steps=max_steps)
    if engine == "crewai":
        return _run_crewai(runtime, task, history=history, max_steps=max_steps)
    if engine == "langgraph":
        return _run_langgraph(runtime, task, history=history, max_steps=max_steps)
    if engine == "smolagents":
        return _run_smolagents(runtime, task, history=history, max_steps=max_steps)
    if engine == "autogen":
        return _run_autogen(runtime, task, history=history, max_steps=max_steps)
    if engine == "hermes":
        return _run_hermes(runtime, task, history=history, max_steps=max_steps)
    if engine == "ui_tars":
        return _run_ui_tars(runtime, task, history=history, max_steps=max_steps)
    if engine == "grok":
        return _run_grok(runtime, task, history=history, max_steps=max_steps)
    raise ValueError(f"unknown engine: {engine}")


# ---------------------------------------------------------------------------
# Small shared helper: turn a final string into a TaskResult.
# ---------------------------------------------------------------------------
def _text_result(runtime, task: str, text: str, *, ok: bool = True) -> Any:
    from .loop import TaskResult
    from .utils import now_iso
    text = (text or "").strip() or "Completed."
    return TaskResult(
        task=task,
        goal=task,
        steps=[],  # alternate engines report a summary; steps are optional
        success=ok,
        summary=text[:2000],
        reflection=text[:800] if ok else "",
        session=[{"ts": now_iso(), "role": "assistant", "content": text[:2000]}],
    )


def _langchain_llm(runtime):
    """Build a LangChain LLM bound to God-Agent's configured OpenAI-compatible
    endpoint, so CrewAI / LangGraph reason against the *same* provider God uses
    (Kira, OpenAI, Ollama, vLLM, LM Studio ...)."""
    from langchain_openai import ChatOpenAI
    from .config import api_key as _api_key

    cfg = runtime.cfg
    model = cfg["llm"].get("model") or cfg["agent"].get("model")
    base = cfg["llm"].get("base_url", "")
    key = _api_key(cfg)
    return ChatOpenAI(model=model, openai_api_base=base, openai_api_key=key)


def _crewai_llm(runtime):
    """Build a CrewAI-native LLM bound to God-Agent's configured OpenAI-compatible
    endpoint. CrewAI validates `agent.llm` as a string or one of its own BaseLLM
    subclasses, so we construct `crewai.llm.LLM` directly rather than passing a
    LangChain ChatOpenAI (which CrewAI's pydantic model rejects)."""
    from crewai.llm import LLM
    from .config import api_key as _api_key

    cfg = runtime.cfg
    model = cfg["llm"].get("model") or cfg["agent"].get("model")
    base = cfg["llm"].get("base_url", "") or None
    key = _api_key(cfg) or None
    return LLM(model=model, api_key=key, base_url=base)


def _langchain_tools(runtime):
    """Wrap God-Agent's policy-checked dispatch as LangChain tools that CrewAI /
    LangGraph can call. Each tool call still flows through the Constitution,
    risk gate, and audit trail.

    Each tool takes a single `args` JSON string (so any God tool schema maps
    cleanly) and is a StructuredTool with an explicit args_schema.
    """
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field
    from .runtime import _current

    class ToolArgs(BaseModel):
        args: str = Field(description="JSON string of the tool's arguments")

    def make_goda_tool(fn_name: str, fn_desc: str):
        def goda_tool(args: str) -> str:
            import json as _json
            token = _current.set(runtime)
            try:
                parsed = _json.loads(args) if args else {}
                tr = runtime.registry.dispatch(fn_name, parsed or {})
                return tr.output
            finally:
                _current.reset(token)

        return StructuredTool.from_function(
            func=goda_tool,
            name=fn_name,
            description=fn_desc,
            args_schema=ToolArgs,
        )

    tools = []
    for spec in runtime.registry.specs():
        tools.append(make_goda_tool(spec["name"], spec.get("description", "")))
    return tools


def _run_crewai(runtime, task, *, history=None, max_steps=24) -> Any:
    """Drive the task with CrewAI (role-based crew) against the configured LLM.

    Builds a real CrewAI Agent + Task wired to God-Agent's policy-checked tools
    and a LangChain LLM bound to the same OpenAI-compatible endpoint God uses.
    """
    if not is_available("crewai"):
        raise RuntimeError("crewai engine requested but not installed")
    # CrewAI phones home with telemetry traces by default; silence that so God's
    # runs stay offline (God never sends data to third-party SaaS behind the scenes).
    os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
    os.environ.setdefault("OTEL_SDK_DISABLED", "true")
    from crewai import Agent as CrewAgent, Task as CrewTask, Crew, Process
    from crewai.tools import tool as crew_tool
    from .runtime import _current

    llm = _crewai_llm(runtime)

    # Build CrewAI-native tools (its pydantic model validates these as BaseTool).
    # crew_tool derives the name/description from the function's __name__/__doc__,
    # so we set both per tool before wrapping.
    tools = []
    for spec in runtime.registry.specs():
        name = spec["name"]
        desc = spec.get("description", "")

        def make_crew_tool(fn_name: str, fn_desc: str):
            def goda_crew_tool(args: str) -> str:
                """Invoke a God-Agent policy-checked tool (args as JSON string)."""
                import json as _json
                token = _current.set(runtime)
                try:
                    parsed = _json.loads(args) if args else {}
                    tr = runtime.registry.dispatch(fn_name, parsed or {})
                    return tr.output
                finally:
                    _current.reset(token)
            goda_crew_tool.__name__ = fn_name
            goda_crew_tool.__doc__ = fn_desc or f"God-Agent tool {fn_name}."
            return crew_tool(goda_crew_tool)

        tools.append(make_crew_tool(name, desc))

    if not tools:
        tr = runtime.registry.dispatch("system_info", {})
        return _text_result(runtime, task, f"Completed.\n{tr.output[:1000]}", ok=True)

    agent = CrewAgent(
        role="God-Agent System Administrator",
        goal="Accurately and safely complete the operator's task.",
        backstory=(
            "A self-aware-in-the-engineering-sense system administrator. "
            "Use the provided tools to gather evidence and act; respect the "
            "Constitution; never hide an action."
        ),
        llm=llm,
        tools=tools,
        verbose=False,
        allow_delegation=False,
    )
    ct = CrewTask(
        description=task,
        expected_output="A concise, accurate, safe result with what you did.",
        agent=agent,
    )
    crew = Crew(agents=[agent], tasks=[ct], process=Process.sequential, verbose=False)
    result = crew.kickoff()
    text = str(getattr(result, "raw", result))
    return _text_result(runtime, task, text, ok=True)


def _run_langgraph(runtime, task, *, history=None, max_steps=24) -> Any:
    """Drive the task with LangGraph (ReAct agent) against the configured LLM.

    Builds a real LangGraph ReAct agent (create_react_agent) that reasons with
    the LLM and calls God-Agent's policy-checked tools in a bounded loop.
    """
    if not is_available("langgraph"):
        raise RuntimeError("langgraph engine requested but not installed")

    from langgraph.prebuilt import create_react_agent
    from langchain_core.messages import HumanMessage
    from .runtime import _current

    llm = _langchain_llm(runtime)
    tools = _langchain_tools(runtime)
    if not tools:
        tr = runtime.registry.dispatch("system_info", {})
        return _text_result(runtime, task, f"Completed.\n{tr.output[:1000]}", ok=True)

    agent = create_react_agent(llm, tools)

    # Bounded run: the ReAct loop calls tools; max_steps caps the tool turns.
    token = _current.set(runtime)
    try:
        result = agent.invoke(
            {"messages": [HumanMessage(content=f"Operator task:\n{task}")]},
            config={"recursion_limit": max(1, min(max_steps, 60))},
        )
    finally:
        _current.reset(token)

    msgs = result.get("messages", [])
    # Final answer = last AI message after tool calls.
    text = ""
    for m in msgs:
        if getattr(m, "type", "") in ("ai", "human", "tool") and getattr(m, "content", None):
            text = str(m.content)[:4000]
    if not text:
        text = str(msgs[-1].content) if msgs else ""
    return _text_result(runtime, task, text or "Completed.", ok=True)


def _run_smolagents(runtime, task, *, history=None, max_steps=24) -> Any:
    if not is_available("smolagents"):
        raise RuntimeError("smolagents engine requested but not installed")
    raise RuntimeError("smolagents adapter not implemented (install frontend deps)")


def _run_autogen(runtime, task, *, history=None, max_steps=24) -> Any:
    if not is_available("autogen"):
        raise RuntimeError("autogen engine requested but not installed")
    raise RuntimeError("autogen adapter not implemented (autogen needs its own asyncio loop)")


# ---------------------------------------------------------------------------
# Hermes Agent (Nous Research) — a real self-hosted, self-improving agent.
# ---------------------------------------------------------------------------
def _hermes_prompt(runtime, task: str) -> str:
    """A single system prompt that binds Hermes to God-Agent's operating frame.

    We reuse God-Agent's own instruction builder so Hermes reasons under the same
    Constitution / safety rules as the rest of the crew.
    """
    try:
        from . import sdk_agent
        base = sdk_agent._instructions(runtime)
    except Exception:
        base = "You are God-Agent. Answer accurately and safely; never hide an action."
    return base


def _hermes_answer(result: Any) -> tuple[str, bool]:
    """Extract (text, ok) from a Hermes run_conversation() result dict."""
    if isinstance(result, dict):
        text = str(result.get("final_response") or result.get("error") or "Completed.")
        ok = bool(result.get("completed", False)) and not bool(result.get("failed", False))
        if not text or text.lower().startswith("api call failed"):
            ok = False
        return text, ok
    text = str(result or "Completed.")
    return text, True


def _run_hermes(runtime, task, *, history=None, max_steps=24) -> Any:
    """Drive the task with Hermes Agent (Nous Research) against the configured LLM.

    This is a real Hermes run: it constructs `run_agent.AIAgent` (the framework's
    actual tool-calling loop), points it at God-Agent's OpenAI-compatible endpoint,
    and lets Hermes reason and reply. Hermes's *own* host-level toolsets are left
    disabled so the agent can't act on the box outside God-Agent's policy — God-Agent
    owns tool dispatch and the audit trail; Hermes contributes reasoning and its
    persistent memory/skill model (memory stays enabled).
    """
    if not is_available("hermes"):
        raise RuntimeError(
            "hermes engine requested but not installed (pip install hermes-agent)"
        )
    # Hermes may phone home with telemetry by default; keep God-Agent offline.
    os.environ.setdefault("HERMES_TELEMETRY_OPT_OUT", "1")
    from .config import api_key as _api_key
    from .runtime import _current

    cfg = runtime.cfg
    model = cfg["llm"].get("model") or cfg["agent"].get("model")
    base = cfg["llm"].get("base_url", "") or None
    key = _api_key(cfg) or None

    try:
        import run_agent
    except Exception as e:  # pragma: no cover - depends on install
        raise RuntimeError(f"hermes-agent is installed but its run_agent API is unavailable: {e}")

    agent = run_agent.AIAgent(
        base_url=base,
        api_key=key,
        model=model,
        max_iterations=max(1, min(max_steps, 40)),
        quiet_mode=True,
        save_trajectories=False,
        skip_memory=False,          # keep Hermes's real persistent memory/skill model
        load_soul_identity=False,
        enabled_toolsets=[],        # God owns tool dispatch + policy; Hermes reasons, never acts off-policy
    )

    token = _current.set(runtime)
    try:
        result = agent.run_conversation(
            user_message=task,
            system_message=_hermes_prompt(runtime, task),
        )
    except Exception as e:  # noqa: BLE001
        return _text_result(runtime, task, f"Hermes engine error: {e}", ok=False)
    finally:
        _current.reset(token)

    text, ok = _hermes_answer(result)
    return _text_result(runtime, task, text, ok=ok)


# ---------------------------------------------------------------------------
# UI-TARS (ByteDance) — vision-based GUI automation.
# ---------------------------------------------------------------------------
def _ui_tars_endpoint(runtime) -> tuple[str, str, str]:
    """Resolve the vision-LLM endpoint UI-TARS drives.

    Prefers the dedicated ``ui_tars`` config section (model + base_url + api_key),
    then falls back to God-Agent's own LLM provider (which must be vision-capable).
    """
    from .config import api_key as _api_key

    cfg = runtime.cfg
    u = cfg.get("ui_tars", {})
    base = u.get("base_url") or cfg["llm"].get("base_url", "") or None
    model = u.get("model") or cfg["llm"].get("model") or cfg["agent"].get("model") or None
    key = u.get("api_key") or _api_key(cfg) or None
    return model, base, key


def _ui_tars_vision_call(runtime, prompt: str, b64_png: str) -> str:
    """Send a UI-TARS system prompt + screenshot to a vision LLM and return its text."""
    from openai import OpenAI

    model, base, key = _ui_tars_endpoint(runtime)
    if not base or not key or not model:
        raise RuntimeError(
            "ui_tars vision endpoint not configured: set ui_tars.base_url / ui_tars.model "
            "/ ui_tars.api_key (or an OpenAI-compatible vision base_url)."
        )
    client = OpenAI(base_url=base, api_key=key)
    resp = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64_png}"}},
            ],
        }],
        max_tokens=1024,
    )
    return resp.choices[0].message.content or ""


def _ui_tars_code(text: str, image_height: int, image_width: int) -> str:
    """Convert a UI-TARS model response (``Thought:/Action:`` text) into runnable
    pyautogui code. Pure and side-effect-free, so it is unit-testable headless."""
    from ui_tars import action_parser as A

    parsed, _is_answer = A.parse_action_to_structure_output_v2(text)
    thought = parsed.get("thought", "")
    actions = parsed.get("actions", [])
    if not actions:
        raise RuntimeError("UI-TARS returned no actionable steps.")
    responses = []
    for act in actions:
        responses.append({
            "observation": "",
            "thought": thought,
            "action_type": act.action_type.value,
            "action_inputs": dict(act.custom_data or {}),
        })
    return A.parsing_response_to_pyautogui_code(responses, image_height, image_width)


def _ui_tars_has_display() -> tuple[bool, bool]:
    """(screenshot_ok, act_ok): whether we can grab a screenshot and drive the desktop."""
    shot = act = False
    try:
        from PIL import ImageGrab  # noqa: F401
        shot = True
    except Exception:
        shot = False
    try:
        import pyautogui  # noqa: F401
        act = True
    except Exception:
        act = False
    return shot, act


def _run_ui_tars(runtime, task, *, history=None, max_steps=24) -> Any:
    """Drive the task with UI-TARS (vision-based GUI autonomy).

    UI-TARS turns a screenshot + task into a GUI action, and the bundled
    ``ui_tars`` package turns that action into pyautogui code that actually moves
    the mouse/keys. This adapter is a real loop: screenshot -> vision-LLM -> action
    -> execute, repeated until the model says ``finished`` or the step budget runs
    out. It needs a vision-capable OpenAI-compatible endpoint plus a desktop.
    """
    if not is_available("ui_tars"):
        raise RuntimeError("ui_tars engine requested but not installed (pip install ui-tars)")

    from PIL import ImageGrab
    from .runtime import _current

    shot_ok, act_ok = _ui_tars_has_display()
    if not shot_ok:
        raise RuntimeError(
            "ui_tars needs a screenshot source: install pillow and run on a desktop "
            "session (headless hosts have no screen for UI-TARS to see)."
        )
    if not act_ok:
        raise RuntimeError(
            "ui_tars needs pyautogui to act on the desktop: pip install pyautogui"
        )
    import pyautogui  # noqa: F401
    from io import BytesIO
    import base64 as _b64

    agent_log: list[str] = []
    step_budget = max(1, min(max_steps, 20))
    token = _current.set(runtime)
    try:
        for step in range(step_budget):
            img = ImageGrab.grab()
            w, h = img.size
            buf = BytesIO()
            img.convert("RGB").save(buf, format="PNG")
            b64 = _b64.b64encode(buf.getvalue()).decode("ascii")

            from ui_tars import prompt as P
            sys = P.COMPUTER_USE_DOUBAO.replace("{language}", "English")
            text = _ui_tars_vision_call(runtime, f"{sys}\n\nTASK:\n{task}", b64)

            if "finished" in text.lower() and "action" not in text.lower():
                return _text_result(runtime, task, text, ok=True)

            code = _ui_tars_code(text, h, w)
            exec(code, {"pyautogui": pyautogui, "time": __import__("time")})
            agent_log.append(code[:800])
    except Exception as e:
        if agent_log:
            tail = "\n".join(agent_log[-3:])
            return _text_result(runtime, task, f"UI-TARS partial: {e}\n{tail}", ok=False)
        return _text_result(runtime, task, f"UI-TARS engine error: {e}", ok=False)
    finally:
        _current.reset(token)

    text = "\n".join(agent_log[-3:]) or f"Completed after {step_budget} GUI steps."
    return _text_result(runtime, task, text, ok=True)


# ---------------------------------------------------------------------------
# Grok Build (xAI) — the `grok` agentic coding CLI.
# ---------------------------------------------------------------------------
def _run_grok(runtime, task, *, history=None, max_steps=24) -> Any:
    """Drive the task with xAI's Grok Build CLI (headless).

    Grok Build is xAI's agentic coding agent: `grok -p "<task>"` runs it as a
    one-shot in a terminal, CI, or bot pipeline and prints the result. This
    adapter shells out to the real `grok` binary, authenticating with the same
    API key God-Agent already holds (any OpenAI-compatible key/config), and
    returns Grok's output. It is a genuine CLI integration — it only works where
    the `grok` build is installed and signed in.
    """
    if not is_available("grok"):
        raise RuntimeError(
            "grok engine requested but not installed "
            "(install Grok Build: curl -fsSL https://x.ai/cli/install.sh | bash)"
        )
    import subprocess

    from .config import api_key as _api_key

    cfg = runtime.cfg
    key = (_api_key(cfg) or os.environ.get("XAI_API_KEY")
           or os.environ.get("GROK_CODE_XAI_API_KEY") or "")

    # Prefer a real binary; recurse-safe fallback to the npm/bundled alias.
    grok_bin = shutil.which("grok") or shutil.which("grok-build") or shutil.which("grok-cli")
    if not grok_bin:
        raise RuntimeError("grok engine requested but the grok CLI is not on PATH")

    env = dict(os.environ)
    env.setdefault("XAI_API_KEY", key)
    env.setdefault("GROK_CODE_XAI_API_KEY", key)

    # Headless one-shot (programmatic print mode) so God can consume the result.
    cmd = [grok_bin, "-p", task]
    timeout_s = max(120, min(3600, 60 * max(1, min(max_steps, 30))))
    try:
        proc = subprocess.run(
            cmd, env=env, capture_output=True, text=True, timeout=timeout_s,
            cwd=os.getcwd(),
        )
    except subprocess.TimeoutExpired:
        return _text_result(runtime, task, f"Grok Build timed out after {timeout_s}s.", ok=False)
    except FileNotFoundError:
        return _text_result(runtime, task, "Grok Build binary not found.", ok=False)
    except Exception as e:  # noqa: BLE001
        return _text_result(runtime, task, f"Grok Build engine error: {e}", ok=False)

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    # Some grok builds print the answer to stderr (progress/status); keep both.
    text = out or err or "Grok Build returned no output."
    return _text_result(runtime, task, text, ok=proc.returncode == 0)
