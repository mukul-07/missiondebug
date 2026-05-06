"""SQLite session index. Schema per SPEC §Phase 4."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  robot_id TEXT NOT NULL,
  started_at INTEGER NOT NULL,
  ended_at INTEGER NOT NULL,
  duration_ms INTEGER NOT NULL,
  label TEXT,
  mcap_path TEXT NOT NULL,
  mcap_size_bytes INTEGER NOT NULL,
  topics_json TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_started_at
  ON sessions(started_at DESC);

CREATE TABLE IF NOT EXISTS annotations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  time_ns INTEGER NOT NULL,
  body TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_annotations_session
  ON annotations(session_id, time_ns);
"""


@dataclass
class AnnotationRow:
    id: int
    session_id: str
    time_ns: int
    body: str
    created_at: int

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> "AnnotationRow":
        return cls(
            id=r["id"],
            session_id=r["session_id"],
            time_ns=r["time_ns"],
            body=r["body"],
            created_at=r["created_at"],
        )


@dataclass
class SessionRow:
    id: str
    robot_id: str
    started_at: int  # unix ms
    ended_at: int
    duration_ms: int
    label: str | None
    mcap_path: str
    mcap_size_bytes: int
    topics: list[str]
    created_at: int

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> "SessionRow":
        return cls(
            id=r["id"],
            robot_id=r["robot_id"],
            started_at=r["started_at"],
            ended_at=r["ended_at"],
            duration_ms=r["duration_ms"],
            label=r["label"],
            mcap_path=r["mcap_path"],
            mcap_size_bytes=r["mcap_size_bytes"],
            topics=json.loads(r["topics_json"]),
            created_at=r["created_at"],
        )


class Db:
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def upsert_session(self, row: SessionRow) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO sessions
                  (id, robot_id, started_at, ended_at, duration_ms, label,
                   mcap_path, mcap_size_bytes, topics_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.id, row.robot_id, row.started_at, row.ended_at,
                    row.duration_ms, row.label, row.mcap_path,
                    row.mcap_size_bytes, json.dumps(row.topics),
                    row.created_at,
                ),
            )
            conn.commit()

    def list_sessions(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        robot_id: str | None = None,
    ) -> list[SessionRow]:
        sql = "SELECT * FROM sessions"
        params: list = []
        if robot_id:
            sql += " WHERE robot_id = ?"
            params.append(robot_id)
        sql += " ORDER BY started_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self.connect() as conn:
            cur = conn.execute(sql, params)
            return [SessionRow.from_row(r) for r in cur.fetchall()]

    def list_robot_ids(self) -> list[str]:
        with self.connect() as conn:
            cur = conn.execute(
                "SELECT DISTINCT robot_id FROM sessions ORDER BY robot_id"
            )
            return [r["robot_id"] for r in cur.fetchall()]

    # ---- annotations -------------------------------------------------

    def insert_annotation(self, session_id: str, time_ns: int, body: str) -> "AnnotationRow":
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO annotations (session_id, time_ns, body, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, time_ns, body, now_ms()),
            )
            new_id = cur.lastrowid
            conn.commit()
            row = conn.execute(
                "SELECT * FROM annotations WHERE id = ?", (new_id,)
            ).fetchone()
            return AnnotationRow.from_row(row)

    def list_annotations(self, session_id: str) -> list["AnnotationRow"]:
        with self.connect() as conn:
            cur = conn.execute(
                "SELECT * FROM annotations WHERE session_id = ? ORDER BY time_ns ASC",
                (session_id,),
            )
            return [AnnotationRow.from_row(r) for r in cur.fetchall()]

    def delete_annotation(self, annotation_id: int) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM annotations WHERE id = ?", (annotation_id,))
            conn.commit()
            return cur.rowcount > 0

    def annotation_counts(self) -> dict[str, int]:
        """Return {session_id: count} for all sessions with annotations."""
        with self.connect() as conn:
            cur = conn.execute(
                "SELECT session_id, COUNT(*) AS n FROM annotations GROUP BY session_id"
            )
            return {r["session_id"]: r["n"] for r in cur.fetchall()}

    def get_session(self, session_id: str) -> SessionRow | None:
        with self.connect() as conn:
            cur = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            r = cur.fetchone()
            return SessionRow.from_row(r) if r else None

    def known_paths(self) -> set[str]:
        with self.connect() as conn:
            cur = conn.execute("SELECT mcap_path FROM sessions")
            return {r["mcap_path"] for r in cur.fetchall()}


def now_ms() -> int:
    return int(time.time() * 1000)
