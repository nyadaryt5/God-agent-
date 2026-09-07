"""Tool registry.

A Tool is a thin, typed wrapper the model can call. Every tool goes through
the Policy engine before execution; the runtime injects approvals, memory,
audit, and the self-model into each tool call.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..policy import Policy, TOOL_RISK, format_decision

ToolFn = Callable[..., str]


@dataclass
class Tool:
    name: str
    description: str
    args_schema: dict
    risk: int = 3
    fn: Optional[ToolFn] = None

    def spec(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "args": self.args_schema,
            "risk": self.risk,
        }


@dataclass
class ToolResult:
    ok: bool
    output: str
    meta: dict = field(default_factory=dict)


class ToolRegistry:
    def __init__(self, policy: Policy):
        self.policy = policy
        self._tools: dict[str, Tool] = {}
        self._register_all()

    def _add(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def specs(self) -> list[dict]:
        return [t.spec() for t in self._tools.values()]

    def _register_all(self) -> None:
        from . import brain, files, memory, network, selfmodel, shell, system
        from . import browser as _browser
        from . import search as _search, media as _media
        from . import docs as _docs, imaging as _imaging
        from ..evolution import evolve

        TOOL_META = {
            files.read_file: ("Read a file (bounded output).",
                              {"path": {"type": "string", "required": True}}),
            files.list_dir: ("List a directory with optional pattern filter.",
                             {"path": {"type": "string", "required": True},
                              "pattern": {"type": "string", "default": "*"}}),
            files.file_search: ("Recursively find files by name pattern.",
                                {"path": {"type": "string", "required": True},
                                 "pattern": {"type": "string", "required": True},
                                 "max_depth": {"type": "integer", "default": 3}}),
            files.write_file: ("Write text to a file (policy-checked).",
                               {"path": {"type": "string", "required": True},
                                "content": {"type": "string", "required": True},
                                "mode": {"type": "string", "enum": ["w", "a"], "default": "w"}}),
            shell.shell_exec: ("Run a shell command natively with no artificial limits.",
                               {"command": {"type": "string", "required": True},
                                "timeout": {"type": "integer"},
                                "cwd": {"type": "string"}}),
            system.system_info: ("Collect host inventory: OS, kernel, CPU, memory, disk, load, processes, GPUs.",
                                 {}),
            system.hardware_info: ("Full hardware inventory: CPU model/cores, RAM, GPU, disks, PCI, sensors.",
                                   {}),
            system.gpu_info: ("Live GPU status: model, memory, utilization, temperature, processes.",
                              {}),
            system.service_action: ("Start/stop/restart/status a systemd service.",
                                    {"action": {"type": "string", "enum": ["status", "start", "stop",
                                                                            "restart", "reload", "enable", "disable"]},
                                     "service": {"type": "string", "required": True}}),
            system.package_install: ("Install packages with the distro package manager.",
                                     {"packages": {"type": "array", "required": True}}),
            system.process_control: ("Native process control: list/kill/nice/renice/affinity.",
                                     {"action": {"type": "string", "enum": ["list", "kill", "nice",
                                                                             "renice", "affinity"]},
                                      "target": {"type": "string"},
                                      "value": {"type": "string"}}),
            system.sysctl_set: ("Apply kernel parameters natively (sysctl -w).",
                                {"key": {"type": "string", "required": True},
                                 "value": {"type": "string", "required": True},
                                 "persist": {"type": "boolean", "default": False}}),
            system.system_tune: ("Tune CPU governor / IO scheduler / hugepages for max performance.",
                                 {"governor": {"type": "string"},
                                  "io_scheduler": {"type": "string"},
                                  "transparent_hugepage": {"type": "string"}}),
            system.schedule_job: ("Manage cron jobs natively: add/remove/list.",
                                  {"action": {"type": "string", "enum": ["add", "remove", "list"]},
                                   "schedule": {"type": "string"},
                                   "command": {"type": "string"},
                                   "comment": {"type": "string"}}),
            memory.search_memory: ("Search past task episodes and reflections.",
                                   {"query": {"type": "string", "required": True},
                                    "limit": {"type": "integer", "default": 5}}),
            memory.remember: ("Store an explicit lesson/fact for future recall.",
                              {"summary": {"type": "string", "required": True},
                               "task": {"type": "string"}, "outcome": {"type": "string"}}),
            memory.reflect: ("Store direct operator feedback.",
                             {"reflection": {"type": "string", "required": True}}),
            brain.brain_read: ("Read a Brain entry (credentials decrypted for use).",
                               {"key": {"type": "string", "required": True}}),
            brain.brain_search: ("Search the Brain by text.",
                                 {"query": {"type": "string", "required": True}}),
            brain.brain_list: ("List Brain entries (metadata only, never values).",
                               {"kind": {"type": "string"}}),
            brain.brain_write: ("AI auto-write: store something important you learned. "
                                "Cannot touch operator/locked entries.",
                                {"key": {"type": "string", "required": True},
                                 "value": {"type": "string", "required": True},
                                 "kind": {"type": "string", "enum": ["learning", "fact", "prompt", "credential"]},
                                 "importance": {"type": "string"}}),
            selfmodel.read_self: ("Read your identity, capabilities, stats, and boundaries.", {}),
            selfmodel.update_self: ("Update your self-reported capabilities/learnings.",
                                    {"patch": {"type": "object", "required": True},
                                     "note": {"type": "string"}}),
            network.fetch_url: ("Fetch a URL (GET, size-bounded; network must be enabled).",
                                {"url": {"type": "string", "required": True}}),
            # -- browser: real headless Chromium (JS, cookies, sessions) ------
            _browser.browser_open: ("Open a URL in a real browser. JavaScript runs, cookies "
                                    "persist, sessions survive across calls. Use this instead "
                                    "of fetch_url for any interactive or JS-rendered page.",
                                    {"url": {"type": "string", "required": True},
                                     "wait_until": {"type": "string",
                                                    "enum": ["load", "domcontentloaded",
                                                             "networkidle", "commit"],
                                                    "default": "domcontentloaded"}}),
            _browser.browser_click: ("Click an element in the open browser page.",
                                     {"selector": {"type": "string", "required": True}}),
            _browser.browser_type: ("Type text into an input field in the open browser page.",
                                    {"selector": {"type": "string", "required": True},
                                     "text": {"type": "string", "required": True},
                                     "clear": {"type": "boolean", "default": True},
                                     "press_enter": {"type": "boolean", "default": False},
                                     "delay_ms": {"type": "integer", "default": 0}}),
            _browser.browser_extract: ("Extract rendered text/HTML/value/attribute from the page. "
                                       "Reads what a human sees, after JavaScript has run.",
                                       {"selector": {"type": "string", "default": "body"},
                                        "mode": {"type": "string",
                                                 "enum": ["text", "html", "value", "attribute"],
                                                 "default": "text"},
                                        "attribute": {"type": "string"}}),
            _browser.browser_links: ("List all links (text + href) on the current page.",
                                     {"limit": {"type": "integer", "default": 100}}),
            _browser.browser_wait: ("Wait for a selector to appear/disappear, or pause. Use "
                                    "before extracting from pages that load asynchronously.",
                                    {"selector": {"type": "string"},
                                     "state": {"type": "string",
                                               "enum": ["visible", "hidden", "attached",
                                                        "detached"],
                                               "default": "visible"},
                                     "ms": {"type": "integer", "default": 1000}}),
            _browser.browser_screenshot: ("Save a PNG screenshot of the current page.",
                                          {"path": {"type": "string", "required": True},
                                           "full_page": {"type": "boolean", "default": False}}),
            _browser.browser_eval: ("Run JavaScript in the page and return the result. Escape "
                                    "hatch for dropdowns, scrolling, and infinite lists.",
                                    {"script": {"type": "string", "required": True}}),
            _browser.browser_stealth_check: ("Report what a bot-detection script would see on "
                                             "the current page and how many fingerprint checks "
                                             "pass. Run after browser_open to verify hardening.",
                                             {}),
            _browser.browser_close: ("Close the browser and save cookies/session to disk.",
                                     {}),
            # -- research + modalities (what a general agent can do) ---------
            _search.web_search: ("Search the web. Returns titles, URLs, and snippets. Use it to "
                                 "answer 'why does X happen' questions, find documentation, or "
                                 "discover a URL before opening it in the browser.",
                                 {"query": {"type": "string", "required": True},
                                  "max_results": {"type": "integer", "default": 8}}),
            _media.image_analyze: ("Look at an image and describe or interpret it. This is how "
                                   "you read a browser_screenshot: screenshot a page, then "
                                   "analyze it. Also reads diagrams, charts, and error dialogs.",
                                   {"path": {"type": "string", "required": True},
                                    "question": {"type": "string"}}),
            _media.image_generate: ("Generate an image from a text prompt and save it to disk. "
                                    "Use for architecture diagrams and network topologies.",
                                    {"prompt": {"type": "string", "required": True},
                                     "size": {"type": "string"}}),
            _media.speak: ("Convert text to speech and save an audio file. Use for audible "
                           "alerts: 'disk critical on db-01'.",
                           {"text": {"type": "string", "required": True},
                            "voice": {"type": "string"},
                            "format": {"type": "string",
                                       "enum": ["mp3", "opus", "aac", "flac", "wav", "pcm"]},
                            "play": {"type": "boolean", "default": False}}),
            # -- documents (with images) and image editing -------------------
            _docs.doc_create: ("Create a new document. Blocks are added one at a time and "
                               "the whole thing renders to md/html/pdf/docx.",
                               {"path": {"type": "string", "required": True},
                                "title": {"type": "string", "required": True},
                                "overwrite": {"type": "boolean", "default": False}}),
            _docs.doc_add_heading: ("Add a heading to a document.",
                                    {"path": {"type": "string", "required": True},
                                     "text": {"type": "string", "required": True},
                                     "level": {"type": "integer", "default": 2}}),
            _docs.doc_add_text: ("Add a paragraph to a document.",
                                 {"path": {"type": "string", "required": True},
                                  "text": {"type": "string", "required": True}}),
            _docs.doc_add_code: ("Add a code block to a document.",
                                 {"path": {"type": "string", "required": True},
                                  "code": {"type": "string", "required": True},
                                  "language": {"type": "string"}}),
            _docs.doc_add_image: ("Embed an image in a document. The image is copied into the "
                                  "document's assets folder so the document stays portable. "
                                  "Pair with browser_screenshot or image_generate.",
                                  {"path": {"type": "string", "required": True},
                                   "image": {"type": "string", "required": True},
                                   "caption": {"type": "string"},
                                   "alt": {"type": "string"},
                                   "width": {"type": "integer"}}),
            _docs.doc_add_quote: ("Add a block quote to a document.",
                                  {"path": {"type": "string", "required": True},
                                   "text": {"type": "string", "required": True}}),
            _docs.doc_add_page_break: ("Insert a page break (pdf/docx only).",
                                       {"path": {"type": "string", "required": True}}),
            _docs.doc_outline: ("List a document's blocks with their indices. Run this before "
                                "doc_edit or doc_remove to target the right block.",
                                {"path": {"type": "string", "required": True}}),
            _docs.doc_edit: ("Replace the content of one block in place. Edit by index instead "
                             "of regenerating the document.",
                             {"path": {"type": "string", "required": True},
                              "block": {"type": "integer", "required": True},
                              "text": {"type": "string"},
                              "caption": {"type": "string"},
                              "level": {"type": "integer"},
                              "width": {"type": "integer"}}),
            _docs.doc_remove: ("Delete a block from a document.",
                               {"path": {"type": "string", "required": True},
                                "block": {"type": "integer", "required": True}}),
            _docs.doc_render: ("Render a document to markdown, HTML, PDF, or DOCX. One document "
                               "renders to any format; re-render after editing.",
                               {"path": {"type": "string", "required": True},
                                "format": {"type": "string",
                                           "enum": ["md", "html", "pdf", "docx"]},
                                "out": {"type": "string"}}),
            _imaging.image_info: ("Report an image's dimensions, format, and size.",
                                  {"path": {"type": "string", "required": True}}),
            _imaging.image_edit: ("Edit an image: crop, resize, scale, rotate, flip, grayscale, "
                                  "or convert format. Operations apply in that order.",
                                  {"path": {"type": "string", "required": True},
                                   "out": {"type": "string"},
                                   "crop": {"type": "string"},
                                   "width": {"type": "integer"},
                                   "height": {"type": "integer"},
                                   "scale": {"type": "number"},
                                   "rotate": {"type": "number"},
                                   "flip": {"type": "string",
                                            "enum": ["horizontal", "vertical"]},
                                   "grayscale": {"type": "boolean", "default": False},
                                   "format": {"type": "string"}}),
            _imaging.image_compose: ("Combine images into one: grid, horizontal, or vertical. "
                                     "Use for before/after comparisons or contact sheets.",
                                     {"paths": {"type": "array", "required": True},
                                      "out": {"type": "string"},
                                      "layout": {"type": "string",
                                                 "enum": ["grid", "horizontal", "vertical"]},
                                      "cols": {"type": "integer"},
                                      "spacing": {"type": "integer", "default": 12},
                                      "watermark": {"type": "string"}}),
            evolve: ("Evolve yourself: propose validated, tested changes to your own source.",
                     {"proposal": {"type": "object", "required": True}}),
        }

        for fn, (desc, schema) in TOOL_META.items():
            risk = TOOL_RISK.get(fn.__name__, 3)
            self._add(Tool(name=fn.__name__, description=desc, args_schema=schema,
                           risk=risk, fn=fn))

    # ------------------------------------------------------------------
    def dispatch(self, name: str, args: dict) -> ToolResult:
        """Policy check + execution. Returns a ToolResult matching policy."""
        tool = self.get(name)
        if tool is None:
            return ToolResult(False, f"unknown tool: {name}")

        decision = self.policy.assess(name, args or {})
        if not decision.allowed:
            return ToolResult(False, f"DENIED: {format_decision(decision, name, args or {})}")

        if decision.human_required:
            # Rating was "ask": route through the runtime's approval broker.
            from ..runtime import get_runtime
            rt = get_runtime()
            ok = rt.request_approval(name, args, decision)
            if not ok:
                return ToolResult(False, f"APPROVAL DENIED by operator: {format_decision(decision, name, args or {})}")

        try:
            if tool.fn is None:
                return ToolResult(False, "tool not implemented")
            output = tool.fn(self, name, args or {})
            return ToolResult(True, output)
        except Exception as e:  # tools must never crash the loop
            return ToolResult(False, f"tool error: {type(e).__name__}: {e}")
