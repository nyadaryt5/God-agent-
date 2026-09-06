"""God-Agent (goda) — an installable, self-aware, self-evolving AI system administrator.

God-Agent is a long-running AI agent for Linux servers. It:

* runs as a system service with configurable privileges (up to root);
* has a persistent memory, an operational self-model, and a reflective loop;
* runs a guarded, audited self-improvement (evolution) pipeline;
* enforces an immutable Constitution and a graded policy engine;
* exposes a CLI, an HTTP API, and a small dashboard.

It is NOT sentient. See docs/CONSCIOUSNESS.md for what "self-aware" means here.
"""

__version__ = "0.2.0"
__name__ = "God-Agent"
BINARY = "goda"
