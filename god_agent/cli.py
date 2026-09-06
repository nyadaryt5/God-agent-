"""Command-line interface for God-Agent (goda)."""
from __future__ import annotations

import sys

if sys.version_info < (3, 10):
    sys.stderr.write(
        f"God-Agent requires Python 3.10 or newer (detected {sys.version.split()[0]}).\n"
        "Please upgrade your Python installation (e.g. `sudo apt install python3.11`).\n"
    )
    sys.exit(1)

import argparse
import json
import os
import secrets
import time
from typing import Optional

# Support running directly: `python3 god_agent/cli.py`
if __name__ == "__main__" and not __package__:
    _pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _pkg_root not in sys.path:
        sys.path.insert(0, _pkg_root)
    __package__ = "god_agent"

from . import __version__
from .audit import AuditLog
from .config import default_config, load_config, save_config
from .llm import get_llm
from .loop import Agent
from .policy import Decision
from .runtime import Runtime
from .utils import Console, ensure_dir, now_iso


def main(argv: Optional[list[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(
        prog="goda",
        description="God-Agent — self-aware, self-evolving AI system administrator.",
    )
    p.add_argument("--config", help="path to config.json")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("init", help="initialize state, config, and API token")

    run_p = sub.add_parser("run", help="run one task")
    run_p.add_argument("task")
    run_p.add_argument("--auto", action="store_true", help="auto-approve high-risk actions")
    run_p.add_argument("--no-reflect", action="store_true", help="skip reflection phase")

    sub.add_parser("desktop", help="open the native desktop app (no web server)")
    sub.add_parser("chat", help="interactive REPL")
    sub.add_parser("serve", help="start the HTTP API + dashboard")
    sub.add_parser("daemon", help="run the watchdog loop (used by systemd)")

    sub.add_parser("status", help="show runtime status")
    sub.add_parser("doctor", help="run selftests and diagnostics")

    mem = sub.add_parser("memory", help="agent memory")
    mem_s = mem.add_subparsers(dest="mem_cmd")
    ms = mem_s.add_parser("search")
    ms.add_argument("query")
    ms.add_argument("--limit", type=int, default=5)
    mr = mem_s.add_parser("remember")
    mr.add_argument("summary")
    mr.add_argument("--outcome", default="ok")

    aud = sub.add_parser("audit", help="audit log")
    aud.add_argument("--verify", action="store_true")
    aud.add_argument("--rotate", action="store_true")
    aud.add_argument("--n", type=int, default=20)

    sub.add_parser("self", help="show self-model")

    st = sub.add_parser("settings", help="open the agent settings (typing 'settings' in chat opens this too)")
    st.add_argument("--list", action="store_true", help="list all settings")
    st.add_argument("key", nargs="?", help="setting key")
    st.add_argument("value", nargs="?", help="setting value")
    st.add_argument("--save", action="store_true", help="save to config file")

    pv = sub.add_parser("providers", help="LLM API providers (custom supported)")
    pv_s = pv.add_subparsers(dest="prov_cmd")
    pv_s.add_parser("list")
    pvt = pv_s.add_parser("test")
    pvt.add_argument("name", nargs="?")
    pva = pv_s.add_parser("add")
    pva.add_argument("name")
    pva.add_argument("--type", default="openai", choices=["openai", "anthropic", "mock", "custom"])
    pva.add_argument("--base-url", default="")
    pva.add_argument("--key", default="")
    pva.add_argument("--key-env", default="")
    pva.add_argument("--model", default="")
    pva.add_argument("--active", action="store_true")
    pvu = pv_s.add_parser("use")
    pvu.add_argument("name")
    pvd = pv_s.add_parser("delete")
    pvd.add_argument("name")

    br = sub.add_parser("brain", help="the Brain (operator commands are ABSOLUTE)")
    br_s = br.add_subparsers(dest="brain_cmd")
    brs = br_s.add_parser("show")
    brs.add_argument("--list", action="store_true", help="list metadata")
    brs.add_argument("--kind", help="filter by kind")
    brr = br_s.add_parser("get")
    brr.add_argument("key")
    brw = br_s.add_parser("set")
    brw.add_argument("key")
    brw.add_argument("value")
    brw.add_argument("--kind", default="fact", choices=["fact", "prompt", "credential", "learning"])
    brw.add_argument("--lock", action="store_true", help="lock against AI writes")
    brw.add_argument("--note", default="")
    brd = br_s.add_parser("delete")
    brd.add_argument("key")
    brl = br_s.add_parser("lock")
    brl.add_argument("key")
    brl.add_argument("--unlock", action="store_true")
    brs2 = br_s.add_parser("size")

    evo = sub.add_parser("evolve", help="evolution pipeline")
    evo.add_argument("proposal", nargs="?", help="path to proposal JSON, or '-' for stdin")
    evo.add_argument("--apply", action="store_true", help="allow auto-apply (overrides config)")
    evo.add_argument("--rollback", action="store_true", help="revert the last evolution commit")

    sub.add_parser("disable", help="halt the agent (kill switch)")
    sub.add_parser("enable", help="lift the kill switch")

    dv = sub.add_parser("dev", help="developer mode (operator-only toggle)")
    dv.add_argument("state", nargs="?", choices=["on", "off", "status"])

    sub.add_parser("version", help="print version")
    args = p.parse_args(argv)

    console = Console()
    if not args.cmd:
        if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
            args.cmd = "desktop"
        else:
            p.print_help()
            return 0

    try:
        if args.cmd == "init":
            return _cmd_init(args, console)
        if args.cmd == "version":
            print(f"God-Agent {__version__}")
            return 0
        cfg = load_config(args.config)
    except Exception as e:  # noqa: BLE001
        console.error(f"config error: {e}")
        return 1

    if args.cmd == "desktop":
        from .desktop import launch
        return launch(cfg)

    if args.cmd in ("run", "chat", "serve", "daemon", "status", "doctor", "memory",
                    "audit", "self", "evolve", "brain", "disable", "enable",
                    "settings", "providers", "dev"):
        try:
            rt = Runtime(cfg, approver=_make_approver(args, console))
        except Exception as e:  # noqa: BLE001
            console.error(f"startup error: {e}")
            return 1

    if args.cmd == "doctor":
        from .selftest import run as selftest
        ok = selftest(verbose=True)
        print(f"uid={os.geteuid()} config={cfg.get('_source', 'defaults')}")
        return 0 if ok else 1
    if args.cmd == "status":
        return _cmd_status(rt, console, cfg)
    if args.cmd == "run":
        return _cmd_run(rt, args, console)
    if args.cmd == "chat":
        return _cmd_chat(rt, args, console)
    if args.cmd == "brain":
        return _cmd_brain(rt, args, console)
    if args.cmd == "settings":
        return _cmd_settings(rt, args, console)
    if args.cmd == "providers":
        return _cmd_providers(rt, args, console)
    if args.cmd == "dev":
        return _cmd_dev(rt, args, console)
    if args.cmd == "serve":
        return _cmd_serve(rt, cfg, console)
    if args.cmd == "daemon":
        return _cmd_daemon(rt, cfg, console)
    if args.cmd == "memory":
        return _cmd_memory(rt, args, console)
    if args.cmd == "audit":
        return _cmd_audit(rt, args, console)
    if args.cmd == "self":
        print(json.dumps(rt.self_model.current(), indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "evolve":
        return _cmd_evolve(rt, args, console)
    if args.cmd == "disable":
        return _cmd_kill(rt, cfg, console, enable=False)
    if args.cmd == "enable":
        return _cmd_kill(rt, cfg, console, enable=True)
    return 0


# ------------------------------------------------------------------ commands #
def _cmd_init(args, console) -> int:
    cfg = default_config()
    root = os.path.expanduser("~/.god-agent")
    ensure_dir(root)
    cfg["state"] = {"root": root,
                    "tasks_dir": os.path.join(root, "tasks")}
    cfg["memory"]["db_path"] = os.path.join(root, "memory.db")
    cfg["self_model"]["path"] = os.path.join(root, "self.json")
    cfg["audit"]["path"] = os.path.join(root, "audit.jsonl")
    cfg["brain"]["path"] = os.path.join(root, "brain.json")
    cfg["kill_switch"]["path"] = os.path.join(root, "DISABLED")
    if not cfg["api"].get("token"):
        cfg["api"]["token"] = secrets.token_urlsafe(32)
    save_config(cfg, os.path.join(root, "config.json"))
    console.ok(f"initialized state in {root}")
    console.info("api token (keep safe):")
    print("  " + cfg["api"]["token"])
    console.info("next: `goda desktop` for the native app, or `goda chat` for terminal chat")
    return 0


def _cmd_run(rt, args, console) -> int:
    if args.auto:
        rt.cfg["policy"]["autonomy"] = "autonomous"
    llm = get_llm(rt.cfg)
    _notice_llm(llm, console)
    agent = Agent(rt, llm, progress=lambda m: console.info(m.rstrip()))
    result = agent.run(args.task)
    console.ok(("SUCCESS" if result.success else "PARTIAL/FAILED") + ": " +
               (result.summary or "(no summary)"))
    if result.reflection:
        console.info("reflection: " + result.reflection)
    if not result.success:
        for s in result.steps:
            if not s.ok:
                console.error(f"  step {s.step} {s.tool}: {s.output[:300]}")
    return 0 if result.success else 1


def _cmd_chat(rt, args, console) -> int:
    llm = get_llm(rt.cfg)
    _notice_llm(llm, console)
    agent = Agent(rt, llm, progress=lambda m: console.info(m.rstrip()))
    print("☩ God-Agent chat. Commands: help · settings · providers · exit")
    history: list[dict] = []
    while True:
        try:
            task = input("\ngoda> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        low = task.lower()
        if low in ("exit", "quit", "q"):
            break
        if not task:
            continue
        if low in ("settings", "/settings"):
            _cmd_settings(rt, argparse.Namespace(list=False, key=None, value=None, save=False), console)
            continue
        if low in ("providers", "/providers"):
            _cmd_providers(rt, argparse.Namespace(prov_cmd="list"), console)
            continue
        if low in ("dev on", "developer mode on", "/dev on"):
            rt.set_dev_mode(True)
            console.warn("DEVELOPER MODE ON — the agent will not refuse commands")
            continue
        if low in ("dev off", "developer mode off", "/dev off"):
            rt.set_dev_mode(False)
            console.ok("developer mode off")
            continue
        if low in ("dev", "developer mode", "/dev"):
            _cmd_dev(rt, argparse.Namespace(state="status"), console)
            continue
        if low in ("help", "/help", "?"):
            print("\n  settings      open the settings menu")
            print("  providers     list LLM providers")
            print("  dev on/off    developer mode — agent will not refuse commands")
            print("  brain         show the Brain")
            print("  status        agent status")
            print("  exit          quit\n")
            continue
        history.append({"role": "user", "content": task})
        t0 = time.time()
        result = agent.run(task, history=history)
        reply = result.summary or "(no summary)"
        print(("✓ " if result.success else "✗ ") + reply)
        for s in result.steps:
            if not s.ok:
                print(f"  ! step {s.step} {s.tool}: {s.output[:200]}")
        history.append({"role": "assistant", "content": reply})
        if result.reflection:
            print(f"  ↻ {result.reflection[:160]}")
        print(f"  ({time.time() - t0:.1f}s)")
    return 0


def _cmd_serve(rt, cfg, console) -> int:
    from .api import GodAgentServer

    llm = get_llm(cfg)
    _notice_llm(llm, console)
    server = GodAgentServer(cfg, runtime=rt, llm=llm)
    port = cfg["api"]["port"]
    host = cfg["api"]["host"]
    t = server.start()
    console.ok(f"API + dashboard at http://{host}:{port}/")
    console.info(f"token: {rt.cfg['api'].get('token', '(none — set one!)')[:12]}...")
    try:
        while t.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
        console.warn("server stopped")
    return 0


def _cmd_daemon(rt, cfg, console) -> int:
    from .watchdog import Watchdog

    wd = Watchdog(rt, get_llm(cfg), interval=int(cfg["agent"].get("watchdog_interval_s", 60)) or 60)
    console.ok("watchdog started")
    try:
        wd.start()
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        wd.stop()
    return 0


def _cmd_status(rt, console, cfg) -> int:
    sm = rt.self_model.current()
    stats = rt.memory.stats()
    ok, errors = rt.audit.verify()
    print(f"God-Agent v{__version__}")
    print(f"  state:     {sm['state'].get('status')}  (since {sm.get('formed_at')[:19]})")
    print(f"  policy:    autonomy={rt.policy.autonomy} approval={rt.policy.approval} "
          f"sandbox={rt.policy.sandbox}")
    print(f"  dev mode:  {'⚡ ON — no refusals' if rt.dev_mode else 'off'}"
          + ("   (goda dev off to disable)" if rt.dev_mode else "   (goda dev on to enable)"))
    print(f"  evolution: {'enabled' if rt.cfg['policy']['evolution'].get('enabled') else 'disabled'}")
    print(f"  memory:    {stats['episodes']} episodes, {stats['reflections']} reflections, "
          f"success rate {stats['successful'] / max(1, stats['episodes']):.0%}")
    print(f"  audit:     {rt.audit.count()} entries, {'VERIFIED' if ok else 'TAMPERED!'}")
    if errors:
        print("  " + "; ".join(errors[:3]))
    print(f"  uid:       {os.geteuid()}")
    return 0


def _cmd_settings(rt, args, console) -> int:
    """Interactive settings menu (or non-interactive: settings <key> <value>)."""
    from .settings import SETTINGS, get_setting, save, set_setting, settings_table

    # Non-interactive set: `goda settings policy.autonomy autonomous`
    if args.key and args.value is not None:
        ok, msg = set_setting(rt.cfg, args.key, args.value)
        if ok:
            rt.reload()
            if args.save:
                path = save(rt.cfg)
                console.ok(f"saved to {path}")
                rt.audit.append("operator", "settings", f"set {args.key} = {args.value}")
            else:
                rt.audit.append("operator", "settings", f"set {args.key} = {args.value} (runtime)")
            console.ok(msg)
        else:
            console.error(msg)
        return 0 if ok else 1

    if args.list:
        for row in settings_table(rt.cfg):
            print(f"  {row['key']:<32} {row['value']!r:<18} {row['label']}")
        return 0

    # Interactive menu
    active = rt.providers.active_profile()
    rows = settings_table(rt.cfg)
    print("\n╔═══════════ God-Agent settings ═══════════╗")
    print(f"║ API provider: {active.get('name','?')} ({active.get('type','?')})")
    if active.get("base_url"):
        print(f"║   base_url: {active['base_url']}")
    if active.get("model"):
        print(f"║   model:    {active['model']}")
    print(f"║ DEV MODE:    {'⚡ ON — agent will not refuse commands' if rt.dev_mode else 'off'}")
    print("╚══════════════════════════════════════════╝")
    idx = 1
    for row in rows:
        print(f"  [{idx}] {row['label']:<34} = {row['value']}")
        idx += 1
    print(f"  [{idx}] API providers   (add custom / switch)")
    print(f"  [{idx+1}] Save & exit")
    print(f"  [{idx+2}] Cancel")
    try:
        choice = input("\nchoice (number or 'key=value', e.g. policy.autonomy=sovereign): ").strip()
    except (EOFError, KeyboardInterrupt):
        return 0
    if not choice:
        return 0
    if choice.isdigit():
        n = int(choice)
        if 1 <= n <= len(rows):
            row = rows[n - 1]
            val = input(f"  {row['key']} [{row['value']}]: ").strip()
            if not val:
                return 0
            ok, msg = set_setting(rt.cfg, row["key"], val)
            if ok:
                rt.reload()
                path = save(rt.cfg)
                console.ok(f"{msg} (saved to {path})")
                rt.audit.append("operator", "settings", f"set {row['key']} = {val}")
            else:
                console.error(msg)
        elif n == len(rows) + 1:
            _cmd_providers(rt, argparse.Namespace(prov_cmd="list"), console)
        return 0
    if "=" in choice:
        key, val = choice.split("=", 1)
        ok, msg = set_setting(rt.cfg, key.strip(), val.strip())
        if ok:
            rt.reload()
            path = save(rt.cfg)
            console.ok(f"{msg} (saved to {path})")
            rt.audit.append("operator", "settings", f"set {key.strip()} = {val.strip()}")
        else:
            console.error(msg)
        return 0 if ok else 1
    return 0


def _cmd_providers(rt, args, console) -> int:
    pm = rt.providers
    cmd = args.prov_cmd
    if cmd == "list":
        print("\nLLM providers (custom supported — any OpenAI-compatible endpoint):")
        for p in pm.list():
            marker = "◀ active" if p["active"] else "        "
            keydesc = "key ✓" if p["has_key"] else ("key=env:" + p["key_env"] if p.get("key_env") else "no key")
            print(f"  {marker} {p['name']:<18} {p['type']:<10} {p['model']:<24} {keydesc}")
            if p["base_url"]:
                print(f"                 {p['base_url']}")
        print("\n  add:   goda providers add NAME --type openai --base-url http://host:11434/v1 --key sk-.. --model llama3.1 --active")
        print("  use:   goda providers use NAME")
        print("  test:  goda providers test NAME")
        return 0
    if cmd == "test":
        name = args.name or pm.active
        result = pm.test(name)
        if result["ok"]:
            console.ok(f"{name}: {result['detail']}")
        else:
            console.error(f"{name}: {result['detail']}")
        return 0 if result["ok"] else 1
    if cmd == "add":
        try:
            pm.add(args.name, ptype=args.type, base_url=args.base_url,
                   api_key=args.key, model=args.model, key_env=args.key_env,
                   active=args.active)
            pm.apply_to(rt.cfg)
            rt.reload()
            console.ok(f"provider '{args.name}' saved" +
                       (" and set ACTIVE" if args.active or len(pm.profiles) == 1 else ""))
            rt.audit.append("operator", "providers", f"added provider '{args.name}' ({args.type})")
            if args.base_url and not args.key:
                console.warn("no API key set — use --key or set the key_env variable")
            _cmd_providers(rt, argparse.Namespace(prov_cmd="list"), console)
        except Exception as e:  # noqa: BLE001
            console.error(f"cannot add provider: {e}")
            return 1
        return 0
    if cmd == "use":
        if not pm.use(args.name):
            console.error(f"no provider named '{args.name}'")
            return 1
        pm.apply_to(rt.cfg)
        rt.reload()
        console.ok(f"active provider: {args.name}")
        rt.audit.append("operator", "providers", f"switched to provider '{args.name}'")
        return 0
    if cmd == "delete":
        if pm.remove(args.name):
            pm.apply_to(rt.cfg)
            rt.reload()
            console.ok(f"removed '{args.name}'")
        else:
            console.error(f"no provider named '{args.name}'")
            return 1
        return 0
    _cmd_providers(rt, argparse.Namespace(prov_cmd="list"), console)
    return 0


def _cmd_dev(rt, args, console) -> int:
    """Developer mode — operator-only toggle. The AI can never enable it."""
    state = args.state or "status"
    if state == "on":
        rt.set_dev_mode(True)
        console.warn("DEVELOPER MODE ON — the agent will not refuse commands. "
                     "All actions remain audited.")
        console.info("turn off with: goda dev off")
        return 0
    if state == "off":
        rt.set_dev_mode(False)
        console.ok("developer mode off — normal policy restored")
        return 0
    banner = "DEVELOPER MODE IS ON" if rt.dev_mode else "developer mode is OFF"
    if rt.dev_mode:
        console.warn(banner)
        console.info("  all actions allowed & audited; the agent will not refuse commands")
    else:
        console.info(banner)
    print("  toggle:  goda dev on|off")
    return 0


def _cmd_brain(rt, args, console) -> int:
    """Operator commands for the Brain — absolute, always win."""
    brain = rt.brain
    cmd = args.brain_cmd
    if cmd == "show":
        if args.list:
            print(json.dumps(brain.list(kind=args.kind), indent=2, ensure_ascii=False))
        else:
            print(json.dumps(brain.size(), indent=2, ensure_ascii=False))
            print(json.dumps(brain.list(kind=args.kind), indent=2, ensure_ascii=False))
        return 0
    if cmd == "get":
        entry = brain.get(args.key)
        if entry is None:
            console.error(f"no entry '{args.key}'")
            return 1
        # Do NOT print credential values to the terminal by default.
        if entry.get("kind") == "credential":
            print(json.dumps({"key": entry["key"], "kind": entry["kind"],
                              "source": entry["source"], "locked": entry["locked"],
                              "value": "*** (credential — see brain_read tool)"},
                             indent=2, ensure_ascii=False))
        else:
            print(json.dumps(entry, indent=2, ensure_ascii=False))
        return 0
    if cmd == "set":
        entry = brain.operator_set(args.key, args.value, kind=args.kind,
                                   locked=args.lock, note=args.note)
        rt.audit.append("operator", "brain_set",
                        f"operator set '{args.key}' ({args.kind})"
                        f"{' [LOCKED]' if args.lock else ''}",
                        {"key": args.key, "kind": args.kind})
        console.ok(f"brain '{args.key}' stored ({args.kind}, "
                   f"{'locked' if args.lock else 'agent-writable'}); operator command is absolute")
        return 0
    if cmd == "delete":
        existed = brain.operator_delete(args.key)
        rt.audit.append("operator", "brain_delete", f"operator deleted '{args.key}'")
        console.ok(f"deleted '{args.key}'" if existed else f"no entry '{args.key}'")
        return 0 if existed else 1
    if cmd == "lock":
        ok = brain.operator_lock(args.key, locked=not args.unlock)
        rt.audit.append("operator", "brain_lock", f"operator {'un' if args.unlock else ''}locked '{args.key}'")
        console.ok(f"{'unlocked' if args.unlock else 'locked'} '{args.key}'" if ok
                   else f"no entry '{args.key}'")
        return 0 if ok else 1
    if cmd == "size":
        print(json.dumps(brain.size(), indent=2, ensure_ascii=False))
        return 0
    console.error("usage: goda brain show|get|set|delete|lock|size")
    return 1


def _cmd_memory(rt, args, console) -> int:
    if args.mem_cmd == "search":
        hits = rt.memory.search(args.query, limit=args.limit)
        print(json.dumps(hits, indent=2, ensure_ascii=False) or "(no results)")
    elif args.mem_cmd == "remember":
        eid = rt.memory.add_episode(args.query or args.summary, args.summary, args.outcome)
        console.ok(f"stored episode #{eid}")
    else:
        console.error("usage: goda memory search|remember ...")
        return 1
    return 0


def _cmd_audit(rt, args, console) -> int:
    log = rt.audit
    if args.verify:
        ok, errors = log.verify()
        if ok:
            console.ok(f"audit log verified ({log.count()} entries, chain intact)")
        else:
            for e in errors[:10]:
                console.error(e)
        return 0 if ok else 1
    if args.rotate:
        target = log.rotate(backup=True)
        console.ok(f"rotated audit log -> {target}")
        return 0
    for e in log.entries(args.n):
        print(f"{e['ts']} [{e['actor']}] {e['action']}: {e['summary']} ({e.get('outcome')})")
    return 0


def _cmd_evolve(rt, args, console) -> int:
    from .evolution import EvolutionPipeline

    pipe = EvolutionPipeline(rt.cfg, rt.memory, rt.audit)
    if args.rollback:
        r = pipe.rollback()
        (console.ok if r["ok"] else console.error)(r.get("output", ""))
        return 0 if r["ok"] else 1
    if not args.proposal:
        console.error("usage: goda evolve proposal.json [--apply] | goda evolve --rollback")
        return 1
    try:
        if args.proposal == "-":
            proposal = json.load(sys.stdin)
        else:
            with open(args.proposal, "r", encoding="utf-8") as fh:
                proposal = json.load(fh)
    except Exception as e:  # noqa: BLE001
        console.error(f"cannot read proposal: {e}")
        return 1
    if args.apply:
        rt.cfg["policy"]["evolution"]["auto_apply"] = True
    result = pipe.run(proposal or {}, source="cli")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def _cmd_kill(rt, cfg, console, enable: bool) -> int:
    """enable=True → remove the kill-switch file (agent runs);
       enable=False → create it (agent halts between steps)."""
    kp = os.path.expanduser(cfg["kill_switch"]["path"])
    if enable:
        if os.path.isfile(kp):
            os.remove(kp)
        rt.audit.append("operator", "kill_switch", "agent re-ENABLED via CLI")
        console.ok("agent enabled")
    else:
        ensure_dir(os.path.dirname(kp))
        with open(kp, "w", encoding="utf-8") as fh:
            fh.write(f"disabled at {now_iso()} by CLI\n")
        rt.audit.append("operator", "kill_switch", "agent DISABLED via CLI")
        console.warn("kill switch ON — agent will halt at the next step")
    return 0


# ------------------------------------------------------------------ helpers #
def _notice_llm(llm, console) -> None:
    note = getattr(llm, "note", None)
    if note:
        console.warn(note)


# ---------------------------------------------------------------- approvals #
def _make_approver(args, console):
    """Interactive CLI approval for high-risk actions (safe default: deny)."""

    def approver(name: str, args: dict, decision: Decision) -> bool:
        print()
        console.warn(f"HIGH-RISK action requires approval: {name}")
        print(f"  reason: {decision.reason}")
        print("  args:   " + json.dumps(args)[:500])
        try:
            answer = input("  approve? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in ("y", "yes")

    return approver


if __name__ == "__main__":
    sys.exit(main())
