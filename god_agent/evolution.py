"""Evolution pipeline — the guarded self-improvement loop.

A proposal is a structured set of changes (new/updated files in the source
tree plus a rationale). The pipeline:

  1. VALIDATE   — schema check, allowed paths only (Constitution/policy/audit
                  code is NON_EVOLVABLE), size limits, no secrets.
  2. BUILD      — materialize a candidate tree: copy the installed package,
                  apply the proposal there.
  3. TEST       — byte-compile everything, run the built-in selftests
                  (and pytest if available) in the candidate tree.
  4. DECIDE     — auto_apply ? apply : create a reviewable patch for a human.
  5. APPLY      — write changes to the live tree in a git repo (or plain copy),
                  commit them, record in memory + audit.
  6. ROLLBACK   — `goda evolution rollback` restores the previous commit/state.

The agent can propose changes; it can never disable this pipeline, change the
Constitution, or touch the policy/audit/LLM code (NON_EVOLVABLE set).
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Optional

from .policy import NON_EVOLVABLE
from .utils import ensure_dir, now_iso, run_command

MAX_FILES = 20
MAX_FILE_BYTES = 300_000
ALLOWED_EXTENSIONS = {".py", ".json", ".md", ".sh"}


class EvolutionError(RuntimeError):
    pass


class EvolutionPipeline:
    def __init__(self, cfg: dict, memory=None, audit=None, repo_root: Optional[str] = None):
        self.cfg = cfg
        self.memory = memory
        self.audit = audit
        self.repo_root = os.path.abspath(repo_root or _package_root())
        self.state_root = os.path.join(cfg["state"]["root"], "evolution")
        ensure_dir(self.state_root)

    # ------------------------------------------------------------------
    def run(self, proposal: dict, *, source: str = "agent") -> dict:
        """Full pipeline. Returns a structured result."""
        try:
            self._validate(proposal)
        except EvolutionError as e:
            self._log(proposal, "rejected", {"stage": "validate", "error": str(e)})
            return {"ok": False, "stage": "validate", "error": str(e)}

        candidate_dir = os.path.join(self.state_root, "candidates", _slug(proposal.get("id", now_iso())))
        ensure_dir(candidate_dir)

        # BUILD candidate tree
        try:
            tree = self._build_candidate(proposal, candidate_dir)
        except EvolutionError as e:
            self._log(proposal, "failed", {"stage": "build", "error": str(e)})
            return {"ok": False, "stage": "build", "error": str(e)}

        # TEST candidate tree
        test_result = self._test_tree(tree)
        if not test_result["ok"]:
            self._log(proposal, "failed", {"stage": "test", **test_result})
            return {"ok": False, "stage": "test", "error": test_result.get("error", "tests failed"),
                    "details": test_result}

        auto = bool(self.cfg["policy"]["evolution"].get("auto_apply", False))
        if not auto:
            patch_path = self._write_patch(proposal, candidate_dir)
            self._log(proposal, "pending_review", {"patch": patch_path, "tests": "passed"})
            return {
                "ok": True, "stage": "pending_review",
                "message": "Proposal passes validation and tests. Auto-apply is off; "
                           "a human must approve it (see patch at " + patch_path + ").",
                "patch": patch_path, "tests": "passed",
            }

        # APPLY
        apply_result = self._apply(proposal)
        if not apply_result["ok"]:
            self._log(proposal, "failed", {"stage": "apply", **apply_result})
            return {"ok": False, "stage": "apply", "error": apply_result.get("error", "apply failed")}
        self._log(proposal, "applied", {"commit": apply_result.get("commit", "")})
        return {"ok": True, "stage": "applied", **apply_result}

    # ------------------------------------------------------------------
    def _validate(self, proposal: dict) -> None:
        if not isinstance(proposal, dict):
            raise EvolutionError("proposal must be an object")
        if proposal.get("constitution") not in (None, "unchanged"):
            raise EvolutionError("proposals may not alter the Constitution")
        files = proposal.get("files")
        if not isinstance(files, list) or not files:
            raise EvolutionError("proposal.files must be a non-empty list")
        if len(files) > MAX_FILES:
            raise EvolutionError(f"too many files ({len(files)} > {MAX_FILES})")
        for f in files:
            path = str(f.get("path", ""))
            rel = _safe_rel(path)
            if rel in NON_EVOLVABLE or rel.startswith("docs/") and rel in ("docs/CONSTITUTION.md", "docs/SAFETY.md"):
                raise EvolutionError(f"non-evolvable path: {rel}")
            if any(rel.startswith(n) for n in ("config/default.json", "install.sh", "uninstall.sh")):
                raise EvolutionError(f"non-evolvable path: {rel}")
            ext = os.path.splitext(rel)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                raise EvolutionError(f"disallowed file type: {rel}")
            content = f.get("content", "")
            if not isinstance(content, str):
                raise EvolutionError(f"content must be a string: {rel}")
            if len(content.encode()) > MAX_FILE_BYTES:
                raise EvolutionError(f"file too large: {rel}")
            if _looks_like_secret(content):
                raise EvolutionError(f"proposal embeds credentials/secrets: {rel}")
            if ext == ".py":
                try:
                    ast.parse(content)
                except SyntaxError as e:
                    raise EvolutionError(f"syntax error in {rel}: {e.msg}") from e
        # must touch at least one code or config file under god_agent/
        if not any(str(f.get("path", "")).startswith(("god_agent/", "config/")) for f in files):
            raise EvolutionError("proposal must touch god_agent/ or config/ files")

    def _build_candidate(self, proposal: dict, target: str) -> str:
        # Copy current package source into the candidate dir.
        src = self.repo_root
        if not os.path.isdir(os.path.join(src, "god_agent")):
            raise EvolutionError(f"source tree not found: {src}")
        if os.path.isdir(target):
            shutil.rmtree(target)
        shutil.copytree(src, target, ignore=shutil.ignore_patterns(
            ".git", "__pycache__", "*.pyc", ".venv", "node_modules"))
        # Apply proposed files.
        for f in proposal["files"]:
            rel = _safe_rel(str(f["path"]))
            dest = os.path.join(target, rel)
            ensure_dir(os.path.dirname(dest))
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(str(f["content"]))
        return target

    def _test_tree(self, tree: str) -> dict:
        # 1. byte-compile all python files
        pyfiles: list[str] = []
        for root, _, files in os.walk(tree):
            if ".git" in root:
                continue
            for f in files:
                if f.endswith(".py"):
                    pyfiles.append(os.path.join(root, f))
        for p in pyfiles:
            try:
                ast.parse(open(p, encoding="utf-8").read())
            except SyntaxError as e:
                return {"ok": False, "error": f"compile error in {os.path.relpath(p, tree)}: {e.msg}"}

        # 2. selftests in the candidate tree (no pytest needed)
        cmd = [sys.executable, "-c",
               "import sys; sys.path.insert(0, '.'); from god_agent.selftest import run; sys.exit(0 if run() else 1)"]
        res = run_command(cmd, timeout=120, cwd=tree)
        if res["exit_code"] != 0:
            return {"ok": False, "error": "selftests failed",
                    "output": (res["stdout"] + res["stderr"])[-4000:]}

        # 3. pytest if available
        if shutil.which("pytest"):
            res2 = run_command(["pytest", "-q"], timeout=300, cwd=tree)
            if res2["exit_code"] != 0:
                return {"ok": False, "error": "pytest failed",
                        "output": (res2["stdout"] + res2["stderr"])[-4000:]}
        return {"ok": True}

    def _write_patch(self, proposal: dict, candidate_dir: str) -> str:
        patch_dir = os.path.join(self.state_root, "patches")
        ensure_dir(patch_dir)
        name = _slug(proposal.get("id", now_iso()))
        git_patch = os.path.join(patch_dir, f"{name}.patch")
        if os.path.isdir(os.path.join(self.repo_root, ".git")):
            _purge_pycache(candidate_dir)
            # Diff against a *clean* copy of the source (no __pycache__ noise).
            clean_dir = os.path.join(self.state_root, "clean")
            if os.path.isdir(clean_dir):
                shutil.rmtree(clean_dir)
            shutil.copytree(self.repo_root, clean_dir, ignore=shutil.ignore_patterns(
                ".git", "__pycache__", "*.pyc", ".venv", "node_modules"))
            diff = subprocess.run(
                ["git", "-C", self.repo_root, "diff", "--no-index",
                 os.path.join(clean_dir, "god_agent"), os.path.join(candidate_dir, "god_agent")],
                capture_output=True, text=True,
            ).stdout
            if diff:
                with open(git_patch, "w", encoding="utf-8") as fh:
                    fh.write(diff)
                return git_patch
        # fallback: JSON bundle
        bundle = os.path.join(patch_dir, f"{name}.json")
        with open(bundle, "w", encoding="utf-8") as fh:
            json.dump(proposal, fh, indent=2, ensure_ascii=False)
        return bundle

    def _apply(self, proposal: dict) -> dict:
        if not os.path.isdir(os.path.join(self.repo_root, ".git")):
            # Non-git install: apply files directly; keep a timestamp bundle for rollback.
            backup_dir = os.path.join(self.state_root, "rollbacks", _slug(now_iso()))
            ensure_dir(backup_dir)
            for f in proposal["files"]:
                rel = _safe_rel(str(f["path"]))
                dest = os.path.join(self.repo_root, rel)
                ensure_dir(os.path.dirname(os.path.abspath(dest)))
                if os.path.isfile(dest):
                    shutil.copy2(dest, os.path.join(backup_dir, rel.replace("/", "_")))
                with open(dest, "w", encoding="utf-8") as fh:
                    fh.write(str(f["content"]))
            with open(os.path.join(backup_dir, "proposal.json"), "w", encoding="utf-8") as fh:
                json.dump(proposal, fh, indent=2)
            return {"ok": True, "method": "copy", "rollback": backup_dir}

        # Git repo: commit on a dedicated branch, then merge to main.
        branch = f"evolution/{_slug(proposal.get('id', now_iso()))}"
        res = run_command(["git", "-C", self.repo_root, "rev-parse", "--abbrev-ref", "HEAD"], timeout=30)
        base_branch = res["stdout"].strip() or "main"
        cmds = [
            ["git", "-C", self.repo_root, "checkout", "-b", branch],
        ]
        for f in proposal["files"]:
            rel = _safe_rel(str(f["path"]))
            dest = os.path.join(self.repo_root, rel)
            ensure_dir(os.path.dirname(os.path.abspath(dest)))
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(str(f["content"]))
        msg = f"goda evolution: {proposal.get('rationale', 'self-improvement')[:120]}"
        cmds += [
            ["git", "-C", self.repo_root, "add", "-A"],
            ["git", "-C", self.repo_root, "commit", "-m", msg],
        ]
        for cmd in cmds:
            r = run_command(cmd, timeout=60)
            if r["exit_code"] != 0 and "commit" not in cmd[1:2]:
                return {"ok": False, "error": (r["stderr"] or r["stdout"])[-500:]}
            if cmd[1:2] == ["commit"] and r["exit_code"] != 0:
                return {"ok": False, "error": (r["stderr"] or r["stdout"])[-500:]}
        # merge back (fast-forward when possible)
        r = run_command(["git", "-C", self.repo_root, "checkout", base_branch], timeout=60)
        if r["exit_code"] == 0:
            run_command(["git", "-C", self.repo_root, "merge", "--no-ff", "-m", f"merge {msg}", branch], timeout=60)
        commit = run_command(["git", "-C", self.repo_root, "rev-parse", "HEAD"], timeout=30)["stdout"].strip()
        return {"ok": True, "method": "git", "branch": branch, "commit": commit[:12]}

    def _log(self, proposal: dict, status: str, result: dict) -> None:
        if self.memory is not None:
            self.memory.log_evolution(proposal, status, result)
        if self.audit is not None:
            self.audit.append("goda", "evolve", f"evolution {status}: "
                              f"{proposal.get('rationale', '(no rationale)')[:160]}",
                              {"status": status, "result": result})

    # ------------------------------------------------------------------
    def rollback(self, *, to_commit: Optional[str] = None) -> dict:
        if os.path.isdir(os.path.join(self.repo_root, ".git")):
            r = run_command(["git", "-C", self.repo_root, "log", "--oneline", "-5"], timeout=30)
            if to_commit:
                rr = run_command(["git", "-C", self.repo_root, "revert", "--no-edit", to_commit], timeout=120)
                return {"ok": rr["exit_code"] == 0,
                        "output": (rr["stdout"] + rr["stderr"])[-1500:],
                        "history": r["stdout"]}
            r2 = run_command(["git", "-C", self.repo_root, "revert", "--no-edit", "HEAD"], timeout=120)
            return {"ok": r2["exit_code"] == 0, "output": (r2["stdout"] + r2["stderr"])[-1500:],
                    "history": r["stdout"]}
        # copy-based rollback: newest backup dir
        backups = os.path.join(self.state_root, "rollbacks")
        if not os.path.isdir(backups):
            return {"ok": False, "output": "no rollback snapshots"}
        dirs = sorted(os.listdir(backups))
        if not dirs:
            return {"ok": False, "output": "no rollback snapshots"}
        latest = os.path.join(backups, dirs[-1])
        for f in os.listdir(latest):
            if f == "proposal.json":
                continue
            shutil.copy2(os.path.join(latest, f), os.path.join(self.repo_root, f))
        return {"ok": True, "output": f"restored {len(os.listdir(latest)) - 1} files from {latest}"}


# ------------------------------------------------------------------ helpers #
def _purge_pycache(root: str) -> None:
    for dirpath, dirnames, filenames in os.walk(root):
        if "__pycache__" in dirnames:
            shutil.rmtree(os.path.join(dirpath, "__pycache__"))
            dirnames.remove("__pycache__")
        for f in filenames:
            if f.endswith((".pyc", ".pyo")):
                try:
                    os.remove(os.path.join(dirpath, f))
                except OSError:
                    pass


def _package_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _safe_rel(path: str) -> str:
    p = path.replace("\\", "/").lstrip("/")
    if p.startswith("../") or "/../" in p or p.startswith("/"):
        raise EvolutionError(f"path escapes package root: {path}")
    if not p:
        raise EvolutionError("empty path in proposal")
    return p


def _slug(text: str) -> str:
    import re as _re

    s = _re.sub(r"[^A-Za-z0-9_.-]+", "-", str(text)).strip("-")[:60]
    return s or "proposal"


def _looks_like_secret(content: str) -> bool:
    lowered = content.lower()
    markers = ("api_key =", "api_key=", "sk-", "aws_secret", "private key", "-----begin",
               "password =", "token =")
    return any(m in lowered for m in markers)


# ------------------------------------------------------------------- tool -- #
def evolve(reg, name: str, args: dict) -> str:
    """Evolve yourself: propose validated, tested changes to your own source."""
    from ..runtime import get_runtime

    rt = get_runtime()
    proposal = args.get("proposal")
    if isinstance(proposal, str):
        try:
            proposal = json.loads(proposal)
        except json.JSONDecodeError:
            return "ERROR: proposal must be valid JSON"
    if not isinstance(proposal, dict):
        return "ERROR: proposal must be an object"

    pipe = EvolutionPipeline(rt.cfg, rt.memory, rt.audit)
    result = pipe.run(proposal, source="agent")
    return json.dumps(result, indent=2, ensure_ascii=False)
