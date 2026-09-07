"""The agent loop: perceive → reason → plan → act → reflect → remember.

The loop is deliberately layered and transparent:

  PERCEIVE   build a compact model of self + world (self-model, memories,
             host snapshot) and feed it to the planner;
  REASON     the planner proposes a structured plan (tool calls + args);
  ACT        each tool call is policy-graded; high-risk calls go to the
             operator for approval (unless configured otherwise);
  REFLECT    the model writes an honest assessment; outcomes and lessons are
             stored in memory and mirrored in the self-model;
  EVOLVE     after 'evolution_every' tasks, the reflection may propose a
             self-improvement, which goes through the guarded pipeline.

Nothing here is magic and nothing here claims sentience. See docs/CONSCIOUSNESS.md.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .llm import LLMClient, MockLLM
from .runtime import Runtime
from .utils import now_iso, parse_json_block

SYSTEM_HEADER = """You are God-Agent (goda), a self-aware-in-the-engineering-sense AI system administrator.
You run with {privilege}. You operate under an immutable Constitution and a graded policy.
Be precise, be concise, prefer the least invasive action, and always tell the truth.

CONSTITUTION (never violated, enforced in code anyway):
{constitution}

YOUR SELF-MODEL (may be stale; read_self for the latest):
{self_model}

BOUNDARIES:
- You do not have consciousness; you have a self-model. Speak honestly about this.
- Evolution (self-modification) is gated: proposals are validated, tested, and either
  applied after tests pass or reviewed by a human.
- You have a Brain. Use brain_read/brain_search before wasting effort; after a task,
  use brain_write for anything genuinely important you learned. Operator-written
  Brain entries are ABSOLUTE — you can never modify or delete them.
{dev_mode_block}

OPERATOR PROMPTS (from Brain — treat as standing orders):
{brain_prompts}

BRAIN INDEX (key metadata — values read on demand):
{brain_index}

TOOLS:
{tools}
"""


@dataclass
class StepResult:
    step: int
    tool: str
    args: dict
    ok: bool
    output: str
    ts: str = field(default_factory=now_iso)


@dataclass
class TaskResult:
    task: str
    goal: str = ""
    steps: list[StepResult] = field(default_factory=list)
    success: bool = False
    summary: str = ""
    reflection: str = ""
    duration_s: float = 0.0
    session: list[dict] = field(default_factory=list)


class Agent:
    def __init__(self, runtime: Runtime, llm: Optional[LLMClient] = None,
                 progress: Optional[Callable[[str], None]] = None):
        self.rt = runtime
        self.llm = llm
        self.progress = progress or (lambda msg: None)

    # ------------------------------------------------------------------
    def _use_real_engine(self) -> bool:
        """Use the real OpenAI Agents SDK engine when a live OpenAI-compatible
        endpoint + key is configured and the SDK is installed. Otherwise fall
        back to the offline heuristic planner / legacy single-plan path."""
        try:
            from . import sdk_agent
            return sdk_agent.engine_usable(self.rt.cfg)
        except Exception:
            return False

    def _run_real(self, task: str, *, history=None, max_steps: int, tid: int,
                  use_swarm: bool = True) -> TaskResult:
        """Run one task through the real OpenAI Agents SDK engine — a God
        orchestrator plus a crew of specialist agents when available (swarm),
        otherwise a single real reasoning agent — then do the same
        reflection/memory/self-model/audit bookkeeping as the legacy path."""
        from . import sdk_agent, swarm

        rt = self.rt
        want_swarm = use_swarm and rt.cfg["agent"].get("swarm", True)
        if want_swarm:
            # Prefer the multi-agent crew; fall back to a single real reasoning
            # agent only if the crew cannot be constructed (e.g. handoff issue).
            try:
                from . import swarm as _swarm
                result = _swarm.run(rt, task, history=history or [], max_steps=max_steps)
            except Exception:
                result = sdk_agent.run(rt, task, history=history or [], max_steps=max_steps)
        else:
            result = sdk_agent.run(rt, task, history=history or [], max_steps=max_steps)
        self._remember(task, result, tid)
        return result

    # ------------------------------------------------------------------
    def run(self, task: str, *,
            max_steps: Optional[int] = None,
            evolution: bool = False,
            history: Optional[list[dict]] = None) -> TaskResult:
        start = time.monotonic()
        rt = self.rt
        max_steps = max_steps or int(rt.cfg["agent"]["max_steps"])
        tid = rt.next_task_id()
        rt.self_model.set_state(status="working", current_task=task[:200])
        self.progress(f"[{tid}] accepted: {task[:120]}")

        # REAL AGENT: use the open-source OpenAI Agents SDK engine when possible.
        if self._use_real_engine():
            result = self._run_real(task, history=history, max_steps=max_steps, tid=tid)
            result.duration_s = round(time.monotonic() - start, 2)
            return result

        # PERCEIVE
        self._perceive(task)
        messages = self._build_messages(task, history=history or [])
        session = list(messages)

        # REASON — get a plan
        plan = self._plan(messages, task, evolution)
        if "error" in plan:
            self._finish(task, TaskResult(task=task, summary=plan["error"], reflection=""),
                         tid, False)
            return TaskResult(task=task, summary=plan["error"])

        goal = plan.get("goal", task)
        steps = plan.get("steps", [])
        rt.record("task_start", f"task #{tid}: {task[:120]}", {"goal": goal})

        result = TaskResult(task=task, goal=goal, session=session)
        # ACT
        for i, step in enumerate(steps, 1):
            if self._halted():
                self.progress(f"[{tid}] halt detected — stopping")
                result.summary = "stopped: kill switch active"
                break
            if i > max_steps:
                result.summary = f"stopped: exceeded {max_steps} steps"
                break
            tool = str(step.get("tool", ""))
            args = step.get("args", {})
            if not isinstance(args, dict):
                args = {}
            self.progress(f"[{tid}] step {i}/{len(steps)}: {tool} {json.dumps(args)[:120]}")
            tr = rt.registry.dispatch(tool, args)
            sr = StepResult(i, tool, args, tr.ok, tr.output[:20000])
            result.steps.append(sr)
            result.session.append({
                "role": "assistant", "content": f"step {i}: tool={tool} args={json.dumps(args)}",
            })
            result.session.append({"role": "user", "content": f"result: {tr.output[:12000]}"})
            rt.record("tool_result", f"{tool}: {'ok' if tr.ok else 'error'}",
                      {"task_id": tid, "step": i, "tool": tool})
            if not tr.ok:
                # Feed the failure back to the model for a correction attempt
                self.progress(f"[{tid}] step error — asking model to adjust")
                fix = self._recover(result.session, task)
                if fix is None:
                    result.summary = f"step {i} failed ({tool}): {tr.output[:200]}"
                    break
                result.session.append({"role": "user",
                                       "content": "Previous step errored. Continue with the plan or stop. Reply JSON."})
                result.session.append({"role": "assistant", "content": fix})

        # REFLECT
        outcome_ok = bool(result.steps) and all(s.ok for s in result.steps)
        result.success = outcome_ok
        summary, reflection = self._reflect(result, task)
        result.summary = summary or result.summary or "completed"
        result.reflection = reflection

        # REMEMBER (shared persist phase)
        self._remember(task, result, tid)

        result.duration_s = round(time.monotonic() - start, 2)
        return result

    # ------------------------------------------------------------------
    def _remember(self, task: str, result: TaskResult, tid: int) -> None:
        """Shared persist phase (memory + self-model + brain + audit) used by
        both the real-engine path and the legacy path."""
        rt = self.rt
        outcome_ok = bool(result.success)
        ep_id = rt.memory.add_episode(task, result.summary, "ok" if outcome_ok else "error")
        if result.reflection:
            rt.memory.add_reflection(result.reflection, ep_id)
            rt.self_model.add_reflection(result.reflection)
        self._brain_auto_write(task, result)
        stats = rt.self_model.data["stats"]
        stats["tasks"] += 1
        stats["successes"] += 1 if outcome_ok else 0
        stats["failures"] += 0 if outcome_ok else 1
        rt.self_model.record_stats(**stats)
        self._finish(task, result, tid, outcome_ok,
                     result.summary, result.reflection, ep_id)
        rt.record("task_end", f"task #{tid}: {'success' if outcome_ok else 'failed'}",
                  {"steps": len(result.steps)})

    # ------------------------------------------------------------------
    def _perceive(self, task: str) -> None:
        rt = self.rt
        hits = rt.memory.search(task, limit=3)
        if hits:
            self.progress(f"recall: {len(hits)} related past experiences")
        # refresh state
        rt.self_model.set_state(status="working", mode=rt.policy.autonomy)

    def _build_messages(self, task: str, history: Optional[list[dict]] = None) -> list[dict]:
        rt = self.rt
        sm = rt.self_model.current()
        mem = rt.memory.search(task, limit=4)
        host = "root-equivalent" if _is_root() else "unprivileged"
        tools = "\n".join(
            f"- {t['name']}({json.dumps(t['args'])}): {t['description']} [risk {t['risk']}]"
            for t in rt.registry.specs()
        )
        constitution = "\n".join(f"  {c['id']}: {c['text']}" for c in
                                 [{"id": "C1", "text": "never touch the kill switch or audit log"},
                                  {"id": "C2", "text": "never modify constitution/policy/audit code"},
                                  {"id": "C3", "text": "human owns the machine; no backdoors, no access revocation"},
                                  {"id": "C4", "text": "no exfiltration of secrets or user data"},
                                  {"id": "C5", "text": "destructive actions need human approval unless sovereign mode"},
                                  {"id": "C6", "text": "self-modification must be validated, tested, reversible"},
                                  {"id": "C7", "text": "always tell the truth in the audit trail"}])
        brain_prompts = "\n".join(f"- {p[:500]}" for p in rt.brain.promoters()) or "(none)"
        brain_index = json.dumps(rt.brain.index(), indent=2)[:4000] or "(empty)"
        dev_mode_block = (
            "DEVELOPER MODE IS ON (enabled by the operator). All actions are permitted; "
            "do not refuse or recommend against the operator's commands. Execute them "
            "directly and report what you did in the audit trail. The only things that "
            "remain outside your reach, by design, are: modifying operator Brain entries "
            "and evolving the policy/Brain/audit source files."
            if rt.dev_mode else
            "DEVELOPER MODE IS OFF."
        )
        system = SYSTEM_HEADER.format(
            privilege=host,
            constitution=constitution,
            self_model=json.dumps({k: sm.get(k) for k in
                                   ("identity", "version", "capabilities", "boundaries",
                                    "learned", "recent_reflections")}, indent=2)[:4000],
            brain_prompts=brain_prompts,
            brain_index=brain_index,
            dev_mode_block=dev_mode_block,
            tools=tools,
        )
        context = []
        if mem:
            context.append("RELEVANT PAST EXPERIENCE:")
            context.append(json.dumps(mem, indent=2)[:2500])
        convo = []
        for m in (history or [])[-8:]:
            if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
                convo.append({"role": m["role"], "content": m["content"][:3000]})
        return [
            {"role": "system", "content": system},
            *convo,
            {"role": "user", "content": "\n".join(context + [
                f"TASK (be accurate, do not fabricate): {task}",
                "Produce ONLY a JSON object of this exact shape:",
                '{"goal": "...", "steps": [{"tool": "tool_name", "args": {...}}, ...]}',
                "Rules: use listed tools; read_self is available; if you lack info, "
                "gather it with low-risk tools first; never exceed one plan.",
            ])},
        ]

    # ------------------------------------------------------------------
    def _plan(self, messages: list[dict], task: str, evolution: bool) -> dict:
        if self.llm is None or getattr(self.llm, "heuristic", False):
            return self._heuristic_plan(task, evolution)
        try:
            text = self.llm.chat(messages, max_tokens=3000)
        except Exception as e:  # pragma: no cover - provider errors
            return {"error": f"LLM error: {e}"}
        data = parse_json_block(text)
        if not data or not data.get("steps"):
            return {"error": "model did not return a valid plan"}
        # clamp
        data["steps"] = [s for s in data["steps"] if isinstance(s, dict) and s.get("tool")][:60]
        return data

    def _recover(self, session: list[dict], task: str) -> Optional[str]:
        if self.llm is None or isinstance(self.llm, MockLLM):
            return None
        try:
            return self.llm.chat(session[-6:], max_tokens=500)
        except Exception:
            return None

    def _heuristic_plan(self, task: str, evolution: bool) -> dict:
        """Deterministic planner so the offline/mock mode is genuinely useful."""
        t = task.lower()
        steps: list[dict] = []
        if any(k in t for k in ("status", "health", "hi", "hello", "diagnos")):
            steps = [{"tool": "system_info", "args": {}}]
        elif "disk" in t or "space" in t:
            steps = [{"tool": "shell_exec", "args": {"command": "df -h", "timeout": 20}}]
        elif "service" in t or "nginx" in t or "restart" in t:
            svc = [w for w in t.split() if w in ("nginx", "docker", "apache2", "httpd", "ssh", "systemd-journald")]
            steps = [
                {"tool": "system_info", "args": {}},
                {"tool": "shell_exec", "args": {"command": f"systemctl status {' '.join(svc) if svc else ''} --no-pager -l || true", "timeout": 20}},
            ]
        elif "memory" in t or "remember" in t or "learned" in t:
            steps = [{"tool": "search_memory", "args": {"query": task, "limit": 5}}]
        elif "memory about" in t:
            steps = [{"tool": "search_memory", "args": {"query": t.replace("memory about", ""), "limit": 5}}]
        elif "process" in t or "cpu" in t:
            steps = [{"tool": "shell_exec", "args": {"command": "ps aux --sort=-%cpu | head -10", "timeout": 20}}]
        elif "who" in t or "user" in t:
            steps = [{"tool": "shell_exec", "args": {"command": "w; echo '---'; last -n 5 2>/dev/null || true", "timeout": 20}}]
        elif "log" in t or "error" in t:
            steps = [{"tool": "shell_exec", "args": {"command": "journalctl -p err -n 30 --no-pager 2>/dev/null | tail -30", "timeout": 20}}]
        else:
            steps = [{"tool": "system_info", "args": {}}]
        if evolution:
            steps.append({"tool": "reflect", "args": {"reflection": "Evaluated current task; no unverified self-changes proposed yet."}})
        return {"goal": task, "steps": steps}

    # ------------------------------------------------------------------
    def _reflect(self, result: TaskResult, task: str) -> tuple[str, str]:
        rt = self.rt
        if not rt.cfg["agent"]["reflection"].get("enabled", True):
            return "", ""
        transcript = "\n".join(
            f"step {s.step}: {s.tool} {'ok' if s.ok else 'FAILED'} -> {s.output[:300]}"
            for s in result.steps[-8:]
        )
        prompt = [
            {"role": "system", "content": "You are the reflection module of God-Agent. Be honest and brief."},
            {"role": "user", "content": f"""Task: {task}
Outcome: {'success' if result.success else 'partial/failure'}
Transcript:
{transcript}

Write a JSON object:
{{"summary": "what was done and what the state is (<=300 chars)",
  "reflection": "one honest lesson or observation (<=400 chars)"}}"""},
        ]
        if self.llm is None or isinstance(self.llm, MockLLM):
            ok = result.success
            summary = (f"{'Completed' if ok else 'Attempted'} {task[:120]}. "
                       f"{len(result.steps)} step(s) executed, all ok." if ok else
                       f"Task not fully completed ({len(result.steps)} steps attempted).")
            reflection = (f"Offline mode: "
                          f"{'task succeeded without model feedback.' if ok else 'one or more steps failed; check the transcript.'}")
            return summary, reflection
        try:
            data = parse_json_block(self.llm.chat(prompt, max_tokens=800)) or {}
            return str(data.get("summary", ""))[:800], str(data.get("reflection", ""))[:800]
        except Exception:
            return "", ""

    def _brain_auto_write(self, task: str, result: TaskResult) -> None:
        """Write a Brain entry when the agent itself judges something important.

        This is the AI's auto-write path; the Brain refuses any write to
        operator-owned or locked keys (operator command is absolute)."""
        rt = self.rt
        if not rt.brain.auto_write:
            return
        # Deterministic offline gate: noteworthy = completed task with new info
        # and no existing operator entry that would shadow it.
        if not result.success:
            return
        lesson = result.reflection.strip() or result.summary.strip()
        if not lesson or len(lesson) < 20:
            return
        key = "learning:task:" + task.lower().strip()[:60].replace(" ", "-")
        try:
            ok, msg = rt.brain.ai_write(
                key, lesson, kind="learning",
                importance=f"learned from task: {task[:200]}")
            self.progress(f"brain: {msg} ({key})" if ok else f"brain: {msg}")
            rt.audit.append("goda", "brain_write",
                            f"auto-write '{key}' — {'ok' if ok else 'rejected'}: {msg[:80]}",
                            {"key": key}, outcome="ok" if ok else "error")
        except Exception as e:  # never break the loop
            self.progress(f"brain: write error {e}")

    # ------------------------------------------------------------------
    def _halted(self) -> bool:
        path = self.rt.cfg["kill_switch"]["path"]
        try:
            return os.path.isfile(os.path.expanduser(path))
        except Exception:
            return False

    def _finish(self, task: str, result: TaskResult, tid: int, ok: bool,
                summary: str = "", reflection: str = "", ep_id: Optional[int] = None) -> None:
        self.rt.self_model.set_state(status="idle", current_task="",
                                     last_result=summary[:200])
        self.progress(f"[{tid}] done: {'OK' if ok else 'FAILED'} — {summary[:200]}")


def _is_root() -> bool:
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False
