"""HTTP API for God-Agent (stdlib only, threaded).

Endpoints:
  GET  /            dashboard UI (static/index.html)
  GET  /health      liveness + version
  GET  /api/status  runtime status (self-model, stats, policy, host)
  POST /api/ask     run a task:  {"task": "...", "interactive": false}
                    interactive=true blocks for operator approval via the UI
  GET  /api/tasks/<id>   current/last task transcript (in-memory ring)
  GET  /api/memory?q=    memory search
  GET  /api/audit        recent audit entries
  GET  /api/evolution    evolution log (as recorded by the pipeline)
  POST /api/evolve       submit an evolution proposal {"proposal": {...}}
  POST /api/disable      enable kill switch ({"reason": "..."})
  GET  /api/notify       long-poll event stream for approval requests

Auth: Bearer token or ?token= from api.token (empty disables auth — dev only).
"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

from .audit import AuditLog
from .config import load_config
from .loop import Agent, TaskResult
from .utils import now_iso


class ApprovalBroker:
    """In-memory broker for operator approvals (used by UI/API)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._pending: dict[str, dict] = {}
        self._answers: dict[str, bool] = {}
        self._waiters: list[threading.Condition] = []

    def request(self, tool: str, args: dict, decision=None) -> bool:
        """Block until the operator answers, or time out (default deny)."""
        key = f"req-{int(time.time() * 1000)}-{tool}"
        info = {
            "tool": tool,
            "args": args,
            "reason": getattr(decision, "reason", ""),
            "risk": getattr(decision, "risk", 0),
            "ts": now_iso(),
        }
        with self._lock:
            self._pending[key] = info
            self._answers.pop(key, None)
            cond = threading.Condition(self._lock)
            self._waiters.append(cond)
        deadline = time.monotonic() + 180
        try:
            with cond:
                while key not in self._answers and time.monotonic() < deadline:
                    cond.wait(timeout=1.0)
                return self._answers.get(key, False)
        finally:
            with self._lock:
                self._pending.pop(key, None)
                if cond in self._waiters:
                    self._waiters.remove(cond)

    def pending(self) -> list[dict]:
        with self._lock:
            return [{"key": k, **v} for k, v in self._pending.items()]

    def answer(self, key: str, approved: bool) -> bool:
        with self._lock:
            if key not in self._pending:
                return False
            self._answers[key] = bool(approved)
            for cond in self._waiters:
                cond.notify_all()
            return True


class GodAgentServer:
    def __init__(self, cfg: Optional[dict] = None, runtime=None, llm=None):
        self.cfg = cfg or load_config()
        self.runtime = runtime
        self.llm = llm
        self.broker = ApprovalBroker()
        self._tasks: dict[int, TaskResult] = {}
        self._lock = threading.Lock()
        self._running_task: Optional[threading.Thread] = None
        self._task_counter = 0

    # --------------------------------------------------------------
    def start(self) -> threading.Thread:
        host = self.cfg["api"].get("host", "0.0.0.0")
        port = int(self.cfg["api"].get("port", 8765))
        server = ThreadingHTTPServer((host, port), self._handler_factory())
        server.daemon_threads = True
        t = threading.Thread(target=server.serve_forever, daemon=True, name="goda-api")
        t.start()
        self._httpd = server
        return t

    def stop(self) -> None:
        if getattr(self, "_httpd", None):
            self._httpd.shutdown()

    # --------------------------------------------------------------
    def submit_task(self, task: str, *, interactive: bool = True,
                    evolution: bool = False, session: str = "default") -> dict:
        with self._lock:
            self._task_counter += 1
            task_id = self._task_counter
        if self._running_task is not None and self._running_task.is_alive():
            return {"error": "one task at a time (agent is busy)", "busy": True}

        rt = self.runtime
        approver = self.broker.request if interactive else None
        # rebind approver if runtime was created without one
        if rt is not None and approver is not None:
            rt.approver = approver
        if rt is not None:
            rt.memory.add_chat(session, "user", task)
            rt.audit.append("operator", "chat", f"chat message ({session}): {task[:120]}")

        # Rebuild the LLM client each submission so provider/settings changes
        # take effect immediately.
        from .llm import get_llm
        self.llm = get_llm(rt.cfg) if rt is not None else self.llm
        history = rt.memory.chat_history(session, limit=12)[:-1] if rt is not None else []
        agent = Agent(rt, self.llm, progress=lambda m: self._log_progress(task_id, m))

        def runner() -> None:
            try:
                if rt is not None:
                    rt.rebind_thread()
                result = agent.run(task, evolution=evolution, history=history)
                with self._lock:
                    self._tasks[task_id] = result
                if rt is not None:
                    rt.memory.add_chat(session, "assistant", result.summary or "(no summary)")
            except Exception as e:  # keep server alive
                with self._lock:
                    self._tasks[task_id] = TaskResult(task=task, summary=f"server error: {e}")
                if rt is not None:
                    rt.memory.add_chat(session, "assistant", f"error: {e}")

        t = threading.Thread(target=runner, daemon=True, name=f"goda-task-{task_id}")
        self._running_task = t
        t.start()
        return {"task_id": task_id, "status": "queued", "session": session}

    def _log_progress(self, tid: int, message: str) -> None:
        print(f"[api] {message.rstrip()}")

    def task(self, task_id: int) -> Optional[dict]:
        with self._lock:
            r = self._tasks.get(task_id)
        if r is None:
            return None
        return {
            "id": task_id,
            "task": r.task,
            "goal": r.goal,
            "success": r.success,
            "summary": r.summary,
            "reflection": r.reflection,
            "steps": [
                {"step": s.step, "tool": s.tool, "ok": s.ok,
                 "output": s.output[:4000]} for s in r.steps
            ],
            "duration_s": r.duration_s,
        }

    def latest(self) -> Optional[dict]:
        with self._lock:
            if not self._tasks:
                return None
            tid = max(self._tasks)
        return self.task(tid)

    # --------------------------------------------------------------
    def _handler_factory(self):
        cfg = self.cfg
        token = cfg["api"].get("token", "")
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "GodAgent/0.1"

            def log_message(self, fmt, *args):  # quieter logs
                return

            def _auth(self) -> bool:
                if not token:
                    return True
                header = self.headers.get("Authorization", "")
                q = ""
                try:
                    from urllib.parse import urlparse, parse_qs
                    q = parse_qs(urlparse(self.path).query).get("token", [""])[0]
                except Exception:
                    pass
                return header == f"Bearer {token}" or q == token

            def _send(self, code: int, body: Any, content_type: str = "application/json") -> None:
                if isinstance(body, (dict, list)):
                    data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
                else:
                    data = str(body).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _json_body(self) -> dict:
                length = int(self.headers.get("Content-Length", 0) or 0)
                if not length:
                    return {}
                try:
                    return json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                except json.JSONDecodeError:
                    return {}

            def do_GET(self):  # noqa: N802
                path = self.path.split("?", 1)[0]
                try:
                    if path in ("", "/"):
                        static = os.path.join(os.path.dirname(__file__), "static", "index.html")
                        with open(static, "r", encoding="utf-8") as fh:
                            html = fh.read().replace("{{API_TOKEN}}", token)
                        self._send(200, html, "text/html; charset=utf-8")
                        return
                    if path == "/health":
                        data = {"ok": True, "service": "god-agent", "ts": now_iso(),
                                "version": __import__("god_agent").__version__}
                        self._send(200, data)
                        return
                    if not self._auth():
                        self._send(401, {"error": "unauthorized"})
                        return
                    if path == "/api/status":
                        self._send(200, owner._status())
                        return
                    if path == "/api/memory":
                        from urllib.parse import urlparse, parse_qs
                        q = parse_qs(urlparse(self.path).query).get("q", [""])[0]
                        rt = owner._rt()
                        self._send(200, {"results": rt.memory.search(q, limit=10) if rt else []})
                        return
                    if path == "/api/audit":
                        rt = owner._rt()
                        if rt:
                            entries = rt.audit.entries(100)
                            ok, errors = rt.audit.verify()
                            self._send(200, {"entries": entries, "verified": ok,
                                             "errors": errors[:5]})
                        else:
                            self._send(200, {"entries": [], "verified": True})
                        return
                    if path == "/api/evolution":
                        rt = owner._rt()
                        self._send(200, {"entries": rt.memory.evolution_entries(30) if rt else []})
                        return
                    if path == "/api/pending":
                        self._send(200, {"pending": owner.broker.pending()})
                        return
                    if path.startswith("/api/task/"):
                        tid = int(path.rsplit("/", 1)[-1])
                        t = owner.task(tid)
                        if t is None:
                            self._send(404, {"error": "no such task"})
                        else:
                            self._send(200, t)
                        return
                    if path == "/api/settings":
                        rt = owner._rt()
                        from .settings import settings_table
                        active = rt.providers.active_profile()
                        self._send(200, {
                            "settings": settings_table(rt.cfg),
                            "provider": {"name": active.get("name"), "type": active.get("type"),
                                         "base_url": active.get("base_url"), "model": active.get("model"),
                                         "has_key": bool(active.get("api_key")),
                                         "key_env": active.get("key_env", "")},
                            "providers": rt.providers.list(),
                            "config_path": __import__("god_agent.settings", fromlist=["persisted_path"]).persisted_path(rt.cfg),
                        })
                        return
                    if path == "/api/providers":
                        rt = owner._rt()
                        self._send(200, {"providers": rt.providers.list(),
                                         "active": rt.providers.active_profile()})
                        return
                    if path == "/api/chat":
                        rt = owner._rt()
                        from urllib.parse import urlparse, parse_qs
                        session = parse_qs(urlparse(self.path).query).get("session", ["default"])[0]
                        self._send(200, {"messages": rt.memory.chat_history(session, limit=100) if rt else []})
                        return
                    if path == "/api/dev":
                        rt = owner._rt()
                        self._send(200, {"enabled": rt.dev_mode,
                                         "note": "operator-controlled; AI cannot enable it",
                                         "toggle": "POST /api/dev {\"enabled\": true|false}"})
                        return
                    if path == "/api/brain":
                        rt = owner._rt()
                        self._send(200, {"size": rt.brain.size(),
                                         "entries": rt.brain.list()})
                        return
                    if path.startswith("/api/brain/"):
                        key = path.rsplit("/", 1)[-1]
                        rt = owner._rt()
                        entry = rt.brain.get(key)
                        if entry is None:
                            self._send(404, {"error": "no such brain entry"})
                        elif entry.get("kind") == "credential":
                            self._send(200, {"key": entry["key"], "kind": entry["kind"],
                                             "source": entry["source"], "locked": entry["locked"],
                                             "value": "***"})
                        else:
                            self._send(200, entry)
                        return
                    self._send(404, {"error": "not found"})
                except Exception as e:  # noqa: BLE001
                    self._send(500, {"error": str(e), "trace": traceback.format_exc()[-1500:]})

            def do_POST(self):  # noqa: N802
                path = self.path.split("?", 1)[0]
                if not self._auth():
                    self._send(401, {"error": "unauthorized"})
                    return
                try:
                    body = self._json_body()
                    if path == "/api/ask":
                        task = str(body.get("task", ""))[:4000]
                        if not task:
                            self._send(400, {"error": "task required"})
                            return
                        interactive = bool(body.get("interactive", True))
                        evolution = bool(body.get("evolution", False))
                        session = str(body.get("session", "default"))[:64]
                        self._send(200, owner.submit_task(task, interactive=interactive,
                                                         evolution=evolution, session=session))
                        return
                    if path == "/api/settings":
                        rt = owner._rt()
                        from .settings import save, set_setting
                        changed = []
                        for key, value in body.items():
                            ok, msg = set_setting(rt.cfg, key, value)
                            changed.append({"key": key, "ok": ok, "msg": msg})
                        if any(c["ok"] for c in changed):
                            rt.reload()
                            saved_to = save(rt.cfg)
                            rt.audit.append("operator", "settings",
                                            f"changed {sum(1 for c in changed if c['ok'])} setting(s)")
                            self._send(200, {"ok": True, "changed": changed, "saved_to": saved_to})
                        else:
                            self._send(400, {"ok": False, "changed": changed})
                        return
                    if path == "/api/providers":
                        rt = owner._rt()
                        action = body.get("action", "test")
                        if action == "add":
                            name = str(body.get("name", ""))[:80]
                            if not name:
                                self._send(400, {"error": "provider name required"})
                                return
                            profile = rt.providers.add(
                                name,
                                ptype=str(body.get("type", "openai"))[:20],
                                base_url=str(body.get("base_url", ""))[:500],
                                api_key=str(body.get("api_key", ""))[:2000],
                                model=str(body.get("model", ""))[:120],
                                key_env=str(body.get("key_env", ""))[:80],
                                active=bool(body.get("active", False)),
                            )
                            rt.providers.apply_to(rt.cfg)
                            rt.reload()
                            rt.audit.append("operator", "providers", f"added '{name}'")
                            self._send(200, {"ok": True, "profile": profile,
                                             "providers": rt.providers.list()})
                            return
                        if action == "use":
                            ok = rt.providers.use(str(body.get("name", "")))
                            if ok:
                                rt.providers.apply_to(rt.cfg)
                                rt.reload()
                                rt.audit.append("operator", "providers",
                                                f"switched to '{body.get('name')}'")
                            self._send(200 if ok else 404, {"ok": ok})
                            return
                        if action == "delete":
                            ok = rt.providers.remove(str(body.get("name", "")))
                            if ok:
                                rt.providers.apply_to(rt.cfg)
                                rt.reload()
                            self._send(200 if ok else 404, {"ok": ok})
                            return
                        # test (default)
                        result = rt.providers.test(body.get("name") or None)
                        self._send(200, result)
                        return
                    if path == "/api/chat/clear":
                        rt = owner._rt()
                        session = str(body.get("session", "default"))[:64]
                        with rt.memory._lock:
                            rt.memory._conn.execute("DELETE FROM chat WHERE session=?", (session,))
                            rt.memory._conn.commit()
                        self._send(200, {"ok": True})
                        return
                    if path == "/api/approve":
                        key = str(body.get("key", ""))
                        ok = owner.broker.answer(key, bool(body.get("approved", False)))
                        self._send(200, {"ok": ok})
                        return
                    if path == "/api/evolve":
                        proposal = body.get("proposal")
                        from .evolution import EvolutionPipeline
                        rt = owner._rt()
                        pipe = EvolutionPipeline(rt.cfg, rt.memory, rt.audit)
                        self._send(200, pipe.run(proposal or {}, source="api"))
                        return
                    if path == "/api/brain/set":
                        rt = owner._rt()
                        key = str(body.get("key", ""))[:128]
                        value = str(body.get("value", ""))
                        kind = str(body.get("kind", "fact"))
                        if not key or not value:
                            self._send(400, {"error": "key and value required"})
                            return
                        entry = rt.brain.operator_set(key, value, kind=kind,
                                                      locked=bool(body.get("locked", False)),
                                                      note=str(body.get("note", ""))[:300])
                        rt.audit.append("operator", "brain_set",
                                        f"API operator set '{key}' ({kind})", {"key": key})
                        self._send(200, {"ok": True, "entry": entry})
                        return
                    if path == "/api/brain/delete":
                        rt = owner._rt()
                        key = str(body.get("key", ""))
                        existed = rt.brain.operator_delete(key)
                        rt.audit.append("operator", "brain_delete", f"API operator deleted '{key}'")
                        self._send(200, {"ok": existed, "message": f"deleted '{key}'" if existed else "not found"})
                        return
                    if path == "/api/dev":
                        rt = owner._rt()
                        enabled = bool(body.get("enabled", False))
                        rt.set_dev_mode(enabled)
                        self._send(200, {"ok": True, "enabled": rt.dev_mode,
                                         "message": "developer mode ON — agent will not refuse commands"
                                         if enabled else "developer mode off"})
                        return
                    if path == "/api/disable":
                        reason = str(body.get("reason", "operator request"))[:300]
                        kp = os.path.expanduser(self.cfg["kill_switch"]["path"])
                        os.makedirs(os.path.dirname(kp), exist_ok=True)
                        with open(kp, "w", encoding="utf-8") as fh:
                            fh.write(f"disabled at {now_iso()} — {reason}\n")
                        rt = owner._rt()
                        if rt:
                            rt.audit.append("operator", "kill_switch", f"DISABLED: {reason}")
                        self._send(200, {"ok": True, "message": "kill switch enabled (agent halted)"})
                        return
                    if path == "/api/enable":
                        kp = os.path.expanduser(self.cfg["kill_switch"]["path"])
                        if os.path.isfile(kp):
                            os.remove(kp)
                        rt = owner._rt()
                        if rt:
                            rt.audit.append("operator", "kill_switch", "ENABLED again")
                        self._send(200, {"ok": True, "message": "agent enabled"})
                        return
                    self._send(404, {"error": "not found"})
                except Exception as e:  # noqa: BLE001
                    self._send(500, {"error": str(e)})

        return Handler

    # --------------------------------------------------------------
    def _rt(self):
        return self.runtime

    def _status(self) -> dict:
        rt = self.runtime
        if rt is None:
            return {"ok": False, "reason": "runtime not initialized"}
        sm = rt.self_model.current()
        return {
            "ok": True,
            "identity": sm.get("identity"),
            "version": sm.get("version"),
            "status": sm.get("state"),
            "capabilities": sm.get("capabilities"),
            "learned": sm.get("learned")[-10:],
            "stats": rt.memory.stats(),
            "developer_mode": rt.dev_mode,
            "brain": rt.brain.size(),
            "execution": {"memory_limit_mb": rt.executor.memory_limit_mb,
                          "cpu_limit_s": rt.executor.cpu_limit_s,
                          "max_processes": rt.executor.max_processes,
                          "native": rt.executor.sandbox in ("none", "local")},
            "provider": {"name": rt.providers.active_profile().get("name"),
                         "type": rt.providers.active_profile().get("type"),
                         "model": rt.cfg["llm"].get("model", ""),
                         "base_url": rt.cfg["llm"].get("base_url", "")},
            "self_model_stats": sm.get("stats"),
            "policy": {"autonomy": rt.policy.autonomy, "approval": rt.policy.approval,
                       "sandbox": rt.policy.sandbox,
                       "evolution_enabled": rt.cfg["policy"]["evolution"].get("enabled", False)},
            "audit": {"entries": rt.audit.count(), "size_mb": round(rt.audit.size_mb(), 2),
                      "verified": rt.audit.verify()[0]},
            "host": {"root": os.geteuid() == 0 if hasattr(os, "geteuid") else False,
                     "platform": os.uname().sysname if hasattr(os, "uname") else "?"},
            "pending_approvals": len(self.broker.pending()),
            "running_task": self.latest(),
        }
