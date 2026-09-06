#!/usr/bin/env python3
"""Systemd entrypoint: starts API server + watchdog for the installed service."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from god_agent.api import GodAgentServer
from god_agent.runtime import Runtime
from god_agent.watchdog import Watchdog
from god_agent.config import load_config
from god_agent.llm import get_llm

def main():
    cfg = load_config()
    rt = Runtime(cfg)
    rt.self_model.set_state(status="idle")
    server = GodAgentServer(cfg, runtime=rt, llm=get_llm(cfg))
    server.start()
    watchdog = Watchdog(rt, get_llm(cfg),
                        interval=int(cfg["agent"].get("watchdog_interval_s", 60)))
    watchdog.start()
    print(f"god-agent daemon ready (uid {os.geteuid()})", flush=True)
    try:
        import time
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        watchdog.stop()
        server.stop()

if __name__ == "__main__":
    main()
