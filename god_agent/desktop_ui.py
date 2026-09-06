"""Tk view for the native desktop controller; never used by CLI/server modes."""
from __future__ import annotations

import getpass
import json
import os
import queue
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from .llm import get_llm
from .providers import default_profile
from .utils import redact

BG = "#11131d"
PANEL = "#1b1e2b"
TEXT = "#edf0f7"
MUTED = "#aab2c6"
ACCENT = "#baabff"


class DesktopWindow:
    def __init__(self, root, controller):
        self.root = root
        self.controller = controller
        self.rt = controller.rt
        self._poll_id = None
        root.title("God-Agent")
        root.geometry("1020x760")
        root.minsize(850, 690)
        root.configure(background=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.option_add("*Font", "{DejaVu Sans} 10")
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, borderwidth=0)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Title.TLabel", font=("DejaVu Sans", 25, "bold"))
        style.configure("Accent.TLabel", foreground=ACCENT)
        style.configure("TButton", background=PANEL, padding=(14, 9))
        style.map("TButton", background=[("active", "#343049"), ("disabled", "#191b25")],
                  foreground=[("disabled", "#626a7d")])
        style.configure("Primary.TButton", background="#54438c")
        style.configure("TNotebook", background=BG, tabmargins=(0, 8, 0, 10))
        style.configure("TNotebook.Tab", background=PANEL, padding=(18, 10))
        style.map("TNotebook.Tab", background=[("selected", "#343049")],
                  foreground=[("selected", TEXT)])
        style.configure("TEntry", fieldbackground=PANEL, insertcolor=TEXT, padding=7)
        style.configure("TCombobox", fieldbackground=PANEL, arrowcolor=TEXT, padding=7)
        style.map("TCombobox", fieldbackground=[("readonly", PANEL)],
                  foreground=[("readonly", TEXT)])
        style.configure("TCheckbutton", background=BG, foreground=TEXT, padding=4)
        root.option_add("*TCombobox*Listbox.background", PANEL)
        root.option_add("*TCombobox*Listbox.foreground", TEXT)

        frame = ttk.Frame(root, padding=24)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        header = ttk.Frame(frame)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="God-Agent", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="NATIVE DESKTOP", style="Accent.TLabel").pack(side="right")
        ttk.Label(frame, text="Runs directly on this system. No browser. No local web server.",
                  style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(5, 10))

        self.tabs = ttk.Notebook(frame)
        self.tabs.grid(row=2, column=0, sticky="nsew")
        self.chat_tab = ttk.Frame(self.tabs)
        self.settings_tab = ttk.Frame(self.tabs, padding=(4, 4))
        self.activity_tab = ttk.Frame(self.tabs)
        self.tabs.add(self.chat_tab, text="Chat")
        self.tabs.add(self.settings_tab, text="AI provider")
        self.tabs.add(self.activity_tab, text="Task output")
        self._chat()
        self._settings()
        self.activity = self._text(self.activity_tab)
        self.activity.pack(fill="both", expand=True)

        footer = ttk.Frame(frame)
        footer.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        self.status = tk.StringVar(value="Ready")
        ttk.Label(footer, textvariable=self.status, style="Accent.TLabel").pack(side="left")
        self.disable_button = ttk.Button(footer, command=self.toggle_disabled)
        self.disable_button.pack(side="right")
        self.identity = tk.StringVar()
        ttk.Label(frame, textvariable=self.identity, style="Muted.TLabel").grid(
            row=4, column=0, sticky="w", pady=(10, 0))
        self._refresh_identity()
        self._load_profile(self.rt.providers.active_profile())
        self._restore_history()
        if getattr(get_llm(self.rt.cfg), "heuristic", False):
            self.tabs.select(self.settings_tab)
            self.provider_note.set("Add your API key and save, or use Chat in offline diagnostic mode.")
        self._poll_id = root.after(100, self._poll)

    @staticmethod
    def _text(parent, **kwargs):
        widget = scrolledtext.ScrolledText(
            parent, wrap="word", background=PANEL, foreground=TEXT,
            insertbackground=TEXT, selectbackground="#54438c", relief="flat",
            borderwidth=0, padx=16, pady=14, font=("DejaVu Sans", 10), **kwargs)
        widget.tag_configure("heading", foreground=ACCENT, font=("DejaVu Sans", 10, "bold"))
        widget.tag_configure("muted", foreground=MUTED)
        widget.configure(state="disabled")
        return widget

    @staticmethod
    def _append(widget, text, tag=""):
        widget.configure(state="normal")
        widget.insert("end", redact(str(text)) + "\n", tag)
        widget.configure(state="disabled")
        widget.see("end")

    def _chat(self):
        self.chat_tab.columnconfigure(0, weight=1)
        self.chat_tab.rowconfigure(0, weight=1)
        self.transcript = self._text(self.chat_tab, height=12)
        self.transcript.grid(row=0, column=0, columnspan=2, sticky="nsew")
        shortcuts = ttk.Frame(self.chat_tab)
        shortcuts.grid(row=1, column=0, columnspan=2, sticky="w", pady=10)
        self.quick_buttons = []
        for label, task in (("System status", "status"), ("Disk usage", "check disk usage")):
            button = ttk.Button(shortcuts, text=label, command=lambda t=task: self.send(t))
            button.pack(side="left", padx=(0, 8))
            self.quick_buttons.append(button)
        self.input = tk.Text(self.chat_tab, height=3, wrap="word", background=PANEL,
                             foreground=TEXT, insertbackground=TEXT, relief="flat",
                             padx=12, pady=10, font=("DejaVu Sans", 11))
        self.input.grid(row=2, column=0, sticky="ew")
        self.input.bind("<Return>", self._enter)
        self.input.bind("<Control-Return>", lambda event: self._send_key())
        self.send_button = ttk.Button(self.chat_tab, text="Send", style="Primary.TButton",
                                      command=self.send)
        self.send_button.grid(row=2, column=1, padx=(10, 0), sticky="ns")
        ttk.Label(self.chat_tab, text="Enter to send · Shift+Enter for a new line · Outputs are in Task output",
                  style="Muted.TLabel").grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _settings(self):
        tab = self.settings_tab
        tab.columnconfigure(1, weight=1)
        ttk.Label(tab, text="The app runs locally; prompts and tool results go to your selected AI provider.",
                  style="Muted.TLabel", wraplength=760).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        self.fields = {}
        self.field_widgets = {}
        rows = (("name", "Provider name"), ("type", "API format"), ("base_url", "Base URL"),
                ("model", "Model ID"), ("api_key", "API key"), ("key_env", "Or key environment variable"))
        for row, (key, label) in enumerate(rows, 1):
            ttk.Label(tab, text=label).grid(row=row, column=0, sticky="w", padx=(0, 20), pady=4)
            variable = tk.StringVar()
            self.fields[key] = variable
            if key in ("name", "type", "model"):
                widget = ttk.Combobox(tab, textvariable=variable)
                if key == "type":
                    widget.configure(values=("openai", "anthropic", "custom", "mock"), state="readonly")
                if key == "name":
                    widget.bind("<<ComboboxSelected>>", self._select_profile)
            else:
                widget = ttk.Entry(tab, textvariable=variable, show="*" if key == "api_key" else "")
            widget.grid(row=row, column=1, sticky="ew", pady=4)
            self.field_widgets[key] = widget
        self.clear_key = tk.BooleanVar(value=False)
        self.clear_key_button = ttk.Checkbutton(tab, text="Remove saved key (environment variable still applies)",
                                                variable=self.clear_key)
        self.clear_key_button.grid(row=7, column=1, sticky="w", pady=(4, 0))
        ttk.Label(tab, text="Blank keeps an existing key. Saved keys stay in your local providers.json (owner-only permissions),\n"
                           "not in the app source. Use a key environment variable if you prefer not to save a key on disk.",
                  style="Muted.TLabel", wraplength=760).grid(row=8, column=0, columnspan=2, sticky="w", pady=10)
        actions = ttk.Frame(tab)
        actions.grid(row=9, column=0, columnspan=2, sticky="w")
        self.save_button = ttk.Button(actions, text="Save & use", style="Primary.TButton", command=self.save_provider)
        self.save_button.pack(side="left", padx=(0, 8))
        self.test_button = ttk.Button(actions, text="Test connection / load models", command=self.test_provider)
        self.test_button.pack(side="left", padx=(0, 8))
        self.preset_button = ttk.Button(actions, text="Kira preset", command=lambda: self._load_profile(default_profile()))
        self.preset_button.pack(side="left")
        self.provider_note = tk.StringVar()
        ttk.Label(tab, textvariable=self.provider_note, style="Accent.TLabel", wraplength=760).grid(
            row=10, column=0, columnspan=2, sticky="w", pady=(14, 0))
        ttk.Label(tab, text="A public model list does not validate a key. If no model-list endpoint exists, Test sends a tiny chat probe.",
                  style="Muted.TLabel", wraplength=760).grid(row=11, column=0, columnspan=2, sticky="w", pady=(10, 0))

    def _load_profile(self, profile):
        for key, variable in self.fields.items():
            variable.set("" if key == "api_key" else profile.get(key, ""))
        self.clear_key.set(False)
        self.field_widgets["name"].configure(values=[p["name"] for p in self.rt.providers.list()])
        self.field_widgets["model"].configure(values=[profile["model"]] if profile.get("model") else [])
        if profile.get("api_key"):
            note = "A key is already saved; leave the key field blank to keep it."
        elif profile.get("key_env") and os.environ.get(profile["key_env"]):
            note = f"A key is available from {profile['key_env']}; it is not saved in this profile."
        else:
            note = "No key is included with the app. Enter your own key, then Save & use."
        self.provider_note.set(note)

    def _select_profile(self, event=None):
        try:
            self._load_profile(self.rt.providers.get(self.fields["name"].get()))
        except KeyError:
            pass

    def _profile(self, *, require_model=True):
        return self.controller.prepare_profile(
            {key: variable.get() for key, variable in self.fields.items()},
            clear_key=self.clear_key.get(), require_model=require_model)

    def save_provider(self):
        try:
            self.controller.save_provider(self._profile())
            self._load_profile(self.rt.providers.active_profile())
            self.provider_note.set("Saved locally. This provider will be used for your next task.")
            self._refresh_identity()
        except Exception as exc:
            messagebox.showerror("Provider settings", redact(str(exc)), parent=self.root)

    def test_provider(self):
        try:
            if self.controller.test_provider(self._profile(require_model=False)):
                self.provider_note.set("Contacting provider…")
                self._set_busy(True)
        except Exception as exc:
            messagebox.showerror("Provider settings", redact(str(exc)), parent=self.root)

    def _restore_history(self):
        history = self.rt.memory.chat_history(self.controller.session, limit=50)
        if not history:
            self._append(self.transcript, "Your system, one conversation away.", "heading")
            self._append(self.transcript, "Ask for a system health check, inspect disk usage, or investigate an error.\n"
                         "Tasks run with your current account's permissions and the configured approval policy.\n", "muted")
        for item in history:
            self._append(self.transcript, "You" if item["role"] == "user" else "God-Agent", "heading")
            self._append(self.transcript, item["content"] + "\n")

    def _enter(self, event):
        if event.state & 0x1:  # Shift+Enter inserts a newline
            return None
        return self._send_key()

    def _send_key(self):
        self.send()
        return "break"

    def send(self, task=None):
        text = task if task is not None else self.input.get("1.0", "end").strip()
        if text.lower() in ("settings", "/settings", "providers"):
            self.tabs.select(self.settings_tab)
            self.input.delete("1.0", "end")
            return
        if self.controller.submit(text):
            self.tabs.select(self.chat_tab)
            self._append(self.transcript, "You", "heading")
            self._append(self.transcript, text + "\n")
            if task is None:
                self.input.delete("1.0", "end")
            self.status.set("Running on this system…")
            self._set_busy(True)
        elif self.controller.disabled:
            self.status.set("Agent disabled. Enable it to start a new task.")

    def _set_busy(self, busy):
        for button in [self.send_button, self.save_button, self.test_button,
                       self.preset_button, self.clear_key_button, *self.quick_buttons]:
            button.state(["disabled"] if busy else ["!disabled"])
        for key, widget in self.field_widgets.items():
            widget.configure(state="disabled" if busy else ("readonly" if key == "type" else "normal"))

    def _refresh_identity(self):
        cfg = self.rt.cfg
        offline = getattr(get_llm(cfg), "heuristic", False)
        mode = "Offline diagnostic planner" if offline else f"{cfg['llm']['model']} · remote AI"
        user = getpass.getuser()
        privilege = "ROOT" if os.geteuid() == 0 else user
        self.identity.set(f"{mode}  |  {privilege}  |  {cfg['policy']['autonomy']} / {cfg['policy']['approval']} approvals"
                          f"  |  sandbox: {cfg['policy']['sandbox']}" + ("  |  DEV MODE ON" if self.rt.dev_mode else ""))
        self.disable_button.configure(text="Enable agent" if self.controller.disabled else "Disable agent")
        if self.controller.disabled:
            self.status.set("Agent disabled")

    def toggle_disabled(self):
        try:
            disabled = not self.controller.disabled
            self.controller.set_disabled(disabled)
            note = ("Disabled: blocks later steps; an active command or AI request is not terminated."
                    if disabled else "Agent enabled")
            self._append(self.activity, note, "muted")
            self._refresh_identity()
            self.status.set(note)
        except Exception as exc:
            messagebox.showerror("Agent control", redact(str(exc)), parent=self.root)

    def _poll(self):
        try:
            for _ in range(100):
                kind, payload = self.controller.events.get_nowait()
                if kind == "progress":
                    self._append(self.activity, payload)
                elif kind == "result":
                    self._append(self.transcript, "God-Agent", "heading")
                    self._append(self.transcript, payload.summary + "\n")
                    if payload.reflection:
                        self._append(self.transcript, payload.reflection + "\n", "muted")
                    for step in payload.steps:
                        self._append(self.activity, f"Step {step.step}: {step.tool}", "heading")
                        self._append(self.activity, step.output + "\n")
                    self.status.set("Completed" if payload.success else "Task incomplete — see Task output")
                elif kind == "error":
                    self._append(self.transcript, "Error: " + payload, "heading")
                    self._append(self.activity, payload)
                    self.status.set("Operation failed — see Task output")
                elif kind == "approval":
                    ApprovalDialog(self.root, payload)
                elif kind == "provider_test":
                    self.provider_note.set(redact(payload["detail"]))
                    if payload.get("models"):
                        self.field_widgets["model"].configure(values=payload["models"])
                # 'idle' is a wakeup; busy is read below after the worker exits.
        except queue.Empty:
            pass
        self._set_busy(self.controller.busy)
        self._poll_id = self.root.after(100, self._poll)

    def close(self):
        if not self.controller.close():
            messagebox.showinfo("Operation still running", "Wait for the current task or provider test to finish before closing.\n\n"
                                "Disable agent blocks later steps, but does not terminate a running system command or AI request.",
                                parent=self.root)
            return
        if self._poll_id:
            self.root.after_cancel(self._poll_id)
        self.root.destroy()


class ApprovalDialog:
    """A scrollable, deny-by-default prompt; only the operator can approve."""

    def __init__(self, root, request):
        self.request = request
        self.window = tk.Toplevel(root)
        self.window.title("God-Agent — approval required")
        self.window.configure(background=BG)
        self.window.transient(root)
        self.window.protocol("WM_DELETE_WINDOW", lambda: self.answer(False))
        frame = ttk.Frame(self.window, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"Allow {request.tool}?", style="Accent.TLabel").pack(anchor="w")
        text = DesktopWindow._text(frame, width=78, height=18)
        text.pack(fill="both", expand=True, pady=12)
        DesktopWindow._append(text, request.reason + "\n\n" + json.dumps(request.args, indent=2, ensure_ascii=False))
        actions = ttk.Frame(frame)
        actions.pack(fill="x")
        deny = ttk.Button(actions, text="Deny", command=lambda: self.answer(False))
        deny.pack(side="left")
        ttk.Button(actions, text="Approve this action", command=lambda: self.answer(True)).pack(side="right")
        self.window.grab_set()
        deny.focus_set()
        self.window.bind("<Escape>", lambda event: self.answer(False))
        self.window.after(100, self._check)

    def answer(self, approved):
        self.request.respond(approved)

    def _check(self):
        if self.request.answered.is_set():
            self.window.grab_release()
            self.window.destroy()
        else:
            self.window.after(100, self._check)
