"""Persistent memory: task episodes, reflections, self-model snapshots.

Search uses a compact TF-IDF vector space implemented in the standard library
so retrieval works offline and with zero heavyweight dependencies.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import threading
from typing import Optional

from .utils import now_iso

TOKEN_RE = re.compile(r"[a-z0-9_]+")
_KEYWORDS = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are",
    "was", "were", "be", "been", "with", "at", "from", "that", "this", "it",
    "as", "by", "not", "but", "do", "did", "has", "have", "had", "we", "you",
}


def tokenize(text: str) -> list[str]:
    words = TOKEN_RE.findall(text.lower())
    return [w for w in words if w not in _KEYWORDS and len(w) > 1]


class MemoryStore:
    def __init__(self, db_path: str, max_episodes: int = 500, max_reflections: int = 1000):
        self.db_path = os.path.abspath(os.path.expanduser(db_path))
        self.max_episodes = max_episodes
        self.max_reflections = max_reflections
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        # check_same_thread=False: the store is shared by the API/task threads.
        self._lock = threading.RLock()  # reentrant: _trim may be called under the lock
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        c = self._conn
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                task TEXT NOT NULL,
                summary TEXT NOT NULL,
                outcome TEXT NOT NULL,
                vector TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reflections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                reflection TEXT NOT NULL,
                vector TEXT NOT NULL,
                task_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS self_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                model_json TEXT NOT NULL,
                note TEXT
            );
            CREATE TABLE IF NOT EXISTS evolution_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                proposal_json TEXT NOT NULL,
                status TEXT NOT NULL,
                result_json TEXT
            );
            CREATE TABLE IF NOT EXISTS chat (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                ts TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    # -- vector space ----------------------------------------------------
    @staticmethod
    def _tf(tokens: list[str]) -> dict[str, float]:
        tf: dict[str, float] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0.0) + 1.0
        total = sum(tf.values()) or 1.0
        return {t: n / total for t, n in tf.items()}

    @staticmethod
    def _cos(a: dict[str, float], b: dict[str, float]) -> float:
        if not a or not b:
            return 0.0
        common = set(a) & set(b)
        dot = sum(a[t] * b[t] for t in common)
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    def _idf(self, corpus: list[dict[str, float]]) -> dict[str, float]:
        n = len(corpus)
        df: dict[str, int] = {}
        for vec in corpus:
            for t in vec:
                df[t] = df.get(t, 0) + 1
        return {t: math.log((1 + n) / (1 + d)) + 1 for t, d in df.items()}

    # -- episodes --------------------------------------------------------
    def add_episode(self, task: str, summary: str, outcome: str = "ok") -> int:
        with self._lock:
            vec = json.dumps(self._tf(tokenize(task + " " + summary)))
            cur = self._conn.execute(
                "INSERT INTO episodes (ts, task, summary, outcome, vector) VALUES (?,?,?,?,?)",
                (now_iso(), task[:1000], summary[:2000], outcome[:200], vec),
            )
            self._conn.commit()
            self._trim("episodes", self.max_episodes)
            return int(cur.lastrowid)

    def search(self, query: str, kind: str = "both", limit: int = 5) -> list[dict]:
        with self._lock:
            return self._search_locked(query, kind, limit)

    def _search_locked(self, query: str, kind: str, limit: int) -> list[dict]:
        q = {k: v * 2.0 for k, v in self._tf(tokenize(query)).items()}  # boost query terms
        results: list[tuple[float, dict]] = []
        if kind in ("episodes", "both"):
            rows = self._conn.execute("SELECT id, ts, task, summary, outcome, vector FROM episodes").fetchall()
            corpus = [json.loads(r[5]) for r in rows]
            idf = self._idf(corpus)
            qw = {t: v * idf.get(t, 1.0) for t, v in q.items()}
            for r, vec in zip(rows, corpus):
                score = self._cos(qw, {t: v * idf.get(t, 1.0) for t, v in vec.items()})
                if score > 0.01:
                    results.append((score, {"kind": "episode", "id": r[0], "ts": r[1],
                                            "task": r[2], "summary": r[3], "outcome": r[4]}))
        if kind in ("reflections", "both"):
            rows = self._conn.execute(
                "SELECT id, ts, reflection, vector FROM reflections"
            ).fetchall()
            corpus = [json.loads(r[3]) for r in rows]
            idf = self._idf(corpus)
            qw = {t: v * idf.get(t, 1.0) for t, v in q.items()}
            for r, vec in zip(rows, corpus):
                score = self._cos(qw, {t: v * idf.get(t, 1.0) for t, v in vec.items()})
                if score > 0.01:
                    results.append((score, {"kind": "reflection", "id": r[0], "ts": r[1],
                                            "reflection": r[2]}))
        results.sort(key=lambda x: -x[0])
        return [dict(item, score=round(s, 4)) for s, item in results[:limit]]

    # -- reflections -----------------------------------------------------
    def add_reflection(self, reflection: str, task_id: Optional[int] = None) -> int:
        with self._lock:
            vec = json.dumps(self._tf(tokenize(reflection)))
            cur = self._conn.execute(
                "INSERT INTO reflections (ts, reflection, vector, task_id) VALUES (?,?,?,?)",
                (now_iso(), reflection[:5000], vec, task_id),
            )
            self._conn.commit()
            self._trim("reflections", self.max_reflections)
            return int(cur.lastrowid)

    def recent_reflections(self, limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, reflection FROM reflections ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [{"id": r[0], "ts": r[1], "reflection": r[2]} for r in reversed(rows)]

    # -- self-model versions ----------------------------------------------
    def save_self_version(self, model: dict, note: str = "") -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO self_versions (ts, model_json, note) VALUES (?,?,?)",
                (now_iso(), json.dumps(model, sort_keys=True), note[:500]),
            )
            self._conn.commit()
        # keep last N versions
        self._conn.execute(
            """DELETE FROM self_versions WHERE id NOT IN
               (SELECT id FROM self_versions ORDER BY id DESC LIMIT 100)"""
        )
        self._conn.commit()
        return int(cur.lastrowid)

    # -- evolution log ----------------------------------------------------
    def log_evolution(self, proposal: dict, status: str, result: Optional[dict] = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO evolution_log (ts, proposal_json, status, result_json) VALUES (?,?,?,?)",
                (now_iso(), json.dumps(proposal, sort_keys=True), status,
                 json.dumps(result, sort_keys=True) if result else None),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def evolution_entries(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, proposal_json, status, result_json FROM evolution_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            try:
                out.append({"id": r[0], "ts": r[1], "proposal": json.loads(r[2]),
                            "status": r[3], "result": json.loads(r[4]) if r[4] else None})
            except json.JSONDecodeError:
                continue
        return out

    # -- chat history ------------------------------------------------------
    def add_chat(self, session: str, role: str, content: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO chat (session, role, content, ts) VALUES (?,?,?,?)",
                (session[:64], role[:16], content[:20000], now_iso()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def chat_history(self, session: str = "default", limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, role, content, ts FROM chat WHERE session=? "
                "ORDER BY id DESC LIMIT ?", (session[:64], limit),
            ).fetchall()
        return [{"id": r[0], "role": r[1], "content": r[2], "ts": r[3]} for r in reversed(rows)]

    # -- stats ------------------------------------------------------------
    def stats(self) -> dict:
        with self._lock:
            ep = self._conn.execute("SELECT COUNT(*), SUM(outcome='ok') FROM episodes").fetchone()
            ref = self._conn.execute("SELECT COUNT(*) FROM reflections").fetchone()
            evo = self._conn.execute("SELECT COUNT(*) FROM evolution_log").fetchone()
        return {"episodes": ep[0], "successful": ep[1] or 0, "failures": (ep[0] or 0) - (ep[1] or 0),
                "reflections": ref[0], "evolutions": evo[0]}

    def _trim(self, table: str, keep: int) -> None:
        with self._lock:
            self._conn.execute(
                f"DELETE FROM {table} WHERE id NOT IN (SELECT id FROM {table} ORDER BY id DESC LIMIT ?)",
                (keep,),
            )
            self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
