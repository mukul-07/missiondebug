"""SQLite session index. v1.5 schema + v2 (fleet) additions."""

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
  created_at INTEGER NOT NULL,
  -- v2 additions (Hard Rule 18: agent stays standalone-capable, so these
  -- are nullable and default to NULL for v1.5 single-robot installs).
  mcap_url TEXT,         -- if set, hub fetches from this URL instead of mcap_path
  subsystem TEXT,        -- free-form domain tag, e.g. "navigation" (Hard Rule 23)
  -- v2 P3.5.1: structured summary computed agent-side at save time.
  -- Deterministic, immutable once written (Hard Rule 27). Null on
  -- pre-P3.5 sessions ingested before this column existed.
  summary TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_started_at
  ON sessions(started_at DESC);
-- idx_sessions_subsystem is created post-migration (see _migrate_v2_columns)
-- because the column doesn't exist on legacy v1.5 databases until ALTER TABLE
-- has run.

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

-- v2 (fleet): one row per agent that has ever reported to this hub.
-- last_heartbeat is updated each ping; agent_url is where the hub
-- proxies MCAP fetches from when a session has no mcap_url set.
CREATE TABLE IF NOT EXISTS agents (
  robot_id TEXT PRIMARY KEY,
  first_seen INTEGER NOT NULL,
  last_heartbeat INTEGER,
  agent_version TEXT,
  agent_url TEXT,
  subsystem TEXT
);

-- v2 (fleet): heartbeat history. TTL-trimmed by prune_old_heartbeats.
-- Used by Phase 2 (operational health) and Phase 3 (fleet observability).
CREATE TABLE IF NOT EXISTS agent_heartbeats (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  robot_id TEXT NOT NULL,
  heartbeat_at INTEGER NOT NULL,
  buffer_size INTEGER,
  FOREIGN KEY (robot_id) REFERENCES agents(robot_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_heartbeats_robot_at
  ON agent_heartbeats(robot_id, heartbeat_at DESC);

-- v2 P3.5.6 (incident memory): per-session resolution state.
-- One row per session that has been triaged. Missing row = implicit
-- "open" status (the default for any new capture). Powers the fleet
-- incident dashboard's resolution-rate + MTTR KPIs.
--
-- Statuses:
--   open           — implicit when no row exists
--   investigating  — someone is actively looking at it
--   resolved       — fixed, root_cause is set
--   duplicate      — this is a recurrence of duplicate_of's pattern
--   wont_fix       — known issue, deliberately not actioned
--
-- resolved_at is set only when status transitions to a terminal state
-- (resolved / duplicate / wont_fix). It is what MTTR aggregation reads.
CREATE TABLE IF NOT EXISTS session_resolutions (
  session_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  root_cause TEXT,
  linked_ticket TEXT,
  duplicate_of TEXT,
  resolved_at INTEGER,
  edited_by TEXT,
  edited_at INTEGER NOT NULL,
  FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_resolutions_status
  ON session_resolutions(status);
CREATE INDEX IF NOT EXISTS idx_resolutions_resolved_at
  ON session_resolutions(resolved_at) WHERE resolved_at IS NOT NULL;
"""


# Per-table v2 column adds. Applied on every Db init so existing v1.5
# databases gain the new columns without manual migration. Each entry
# is (table, column, type_def).
_V2_COLUMN_ADDS = [
    ("sessions", "mcap_url", "TEXT"),
    ("sessions", "subsystem", "TEXT"),
    ("sessions", "summary", "TEXT"),
]


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
    # v2 additions — optional, default None for v1.5 callers.
    mcap_url: str | None = None
    subsystem: str | None = None
    summary: str | None = None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> "SessionRow":
        keys = r.keys()
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
            mcap_url=r["mcap_url"] if "mcap_url" in keys else None,
            subsystem=r["subsystem"] if "subsystem" in keys else None,
            summary=r["summary"] if "summary" in keys else None,
        )


@dataclass
class AgentRow:
    robot_id: str
    first_seen: int
    last_heartbeat: int | None
    agent_version: str | None
    agent_url: str | None
    subsystem: str | None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> "AgentRow":
        return cls(
            robot_id=r["robot_id"],
            first_seen=r["first_seen"],
            last_heartbeat=r["last_heartbeat"],
            agent_version=r["agent_version"],
            agent_url=r["agent_url"],
            subsystem=r["subsystem"],
        )


# v2 P3.5.6 resolution statuses. Kept here (not as an enum) because SQLite
# stores them as TEXT and Pydantic Field(pattern=...) does runtime validation
# — a Python enum would duplicate the canonical list without adding safety.
RESOLUTION_STATUSES = ("open", "investigating", "resolved", "duplicate", "wont_fix")
TERMINAL_STATUSES = frozenset({"resolved", "duplicate", "wont_fix"})


@dataclass
class ResolutionRow:
    session_id: str
    status: str
    root_cause: str | None
    linked_ticket: str | None
    duplicate_of: str | None
    resolved_at: int | None
    edited_by: str | None
    edited_at: int

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> "ResolutionRow":
        return cls(
            session_id=r["session_id"],
            status=r["status"],
            root_cause=r["root_cause"],
            linked_ticket=r["linked_ticket"],
            duplicate_of=r["duplicate_of"],
            resolved_at=r["resolved_at"],
            edited_by=r["edited_by"],
            edited_at=r["edited_at"],
        )

    @classmethod
    def implicit_open(cls, session_id: str) -> "ResolutionRow":
        """An untriaged session has no row; this is what we hand callers
        instead of None so the UI doesn't have to branch on null."""
        return cls(
            session_id=session_id,
            status="open",
            root_cause=None,
            linked_ticket=None,
            duplicate_of=None,
            resolved_at=None,
            edited_by=None,
            edited_at=0,
        )


@dataclass
class FleetSessionRow:
    """Denormalised session + resolution view used by the v2 P3.5.6b
    fleet stats endpoint. Lets the dashboard scan the whole window in
    a single query and compute every KPI in one Python pass."""

    id: str
    robot_id: str
    label: str | None
    started_at: int  # unix ms
    subsystem: str | None
    summary: str | None
    res_status: str | None  # NULL => implicit "open"
    res_resolved_at: int | None
    res_duplicate_of: str | None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> "FleetSessionRow":
        return cls(
            id=r["id"],
            robot_id=r["robot_id"],
            label=r["label"],
            started_at=r["started_at"],
            subsystem=r["subsystem"],
            summary=r["summary"],
            res_status=r["res_status"],
            res_resolved_at=r["res_resolved_at"],
            res_duplicate_of=r["res_duplicate_of"],
        )

    @property
    def effective_status(self) -> str:
        """The resolution status the dashboard reads. NULL row → 'open'."""
        return self.res_status or "open"


class Db:
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate_v2_columns(conn)
            conn.commit()

    def _migrate_v2_columns(self, conn: sqlite3.Connection) -> None:
        """Add v2 columns to existing v1.5 databases without forcing a manual
        migration. SQLite's CREATE TABLE IF NOT EXISTS doesn't alter existing
        tables, so we check pragma + ALTER TABLE column-by-column. Idempotent.
        """
        for table, column, type_def in _V2_COLUMN_ADDS:
            cur = conn.execute(f"PRAGMA table_info({table})")
            existing = {row[1] for row in cur.fetchall()}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {type_def}")
        # Indexes on v2 columns: created here, after the columns exist.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_subsystem "
            "ON sessions(subsystem) WHERE subsystem IS NOT NULL"
        )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        # SQLite ships with FK enforcement off; the annotations FK only
        # cascades if we turn it on per-connection.
        conn.execute("PRAGMA foreign_keys = ON")
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
                   mcap_path, mcap_size_bytes, topics_json, created_at,
                   mcap_url, subsystem, summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.id, row.robot_id, row.started_at, row.ended_at,
                    row.duration_ms, row.label, row.mcap_path,
                    row.mcap_size_bytes, json.dumps(row.topics),
                    row.created_at, row.mcap_url, row.subsystem, row.summary,
                ),
            )
            conn.commit()

    def attach_mcap_path(self, session_id: str, mcap_path: str) -> None:
        """Set mcap_path on an existing session without touching any other
        column. The scanner uses this to make a hub-ingested session (which
        carries summary/subsystem but no local bytes) servable from a local
        file, without an INSERT OR REPLACE that would clobber those fields.
        """
        with self.connect() as conn:
            conn.execute(
                "UPDATE sessions SET mcap_path = ? WHERE id = ?",
                (mcap_path, session_id),
            )
            conn.commit()

    def list_sessions(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        robot_id: str | None = None,
        subsystem: str | None = None,
    ) -> list[SessionRow]:
        sql = "SELECT * FROM sessions"
        clauses: list[str] = []
        params: list = []
        if robot_id:
            clauses.append("robot_id = ?")
            params.append(robot_id)
        if subsystem:
            clauses.append("subsystem = ?")
            params.append(subsystem)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY started_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self.connect() as conn:
            cur = conn.execute(sql, params)
            return [SessionRow.from_row(r) for r in cur.fetchall()]

    def list_past_sessions_with_summary(
        self,
        *,
        before_started_at: int,
        exclude_id: str,
        limit: int = 5000,
    ) -> list[SessionRow]:
        """Past sessions (strictly older than `before_started_at`) that have
        a non-null summary. Used by the v2 P3.5.2 similarity-search endpoint
        to build the corpus for "Has this happened before?".

        Strictly past — the query session and any later sessions are
        excluded. `limit` is a backstop for very large fleets; the default
        of 5000 keeps a single similarity query under ~100ms even with
        pure-Python TF-IDF. Smarter shard/cache strategies are a v2.x
        problem when a real fleet crosses that bar.
        """
        with self.connect() as conn:
            cur = conn.execute(
                """
                SELECT * FROM sessions
                WHERE started_at < ?
                  AND id != ?
                  AND summary IS NOT NULL
                  AND length(summary) > 0
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (before_started_at, exclude_id, limit),
            )
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

    def update_annotation(self, annotation_id: int, body: str) -> "AnnotationRow | None":
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE annotations SET body = ? WHERE id = ?",
                (body, annotation_id),
            )
            conn.commit()
            if cur.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT * FROM annotations WHERE id = ?", (annotation_id,)
            ).fetchone()
            return AnnotationRow.from_row(row)

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

    # ---- v2 agents + heartbeats ------------------------------------

    def upsert_agent(
        self,
        *,
        robot_id: str,
        agent_url: str | None = None,
        agent_version: str | None = None,
        subsystem: str | None = None,
    ) -> None:
        """Register or update an agent. Called by the ingest + heartbeat
        endpoints. first_seen is set on initial insert and never overwritten.
        Other fields are updated when provided (NULL leaves them alone)."""
        ts = now_ms()
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT robot_id FROM agents WHERE robot_id = ?", (robot_id,)
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO agents
                      (robot_id, first_seen, agent_url, agent_version, subsystem)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (robot_id, ts, agent_url, agent_version, subsystem),
                )
            else:
                # Only overwrite columns the caller actually supplied.
                sets, params = [], []
                if agent_url is not None:
                    sets.append("agent_url = ?")
                    params.append(agent_url)
                if agent_version is not None:
                    sets.append("agent_version = ?")
                    params.append(agent_version)
                if subsystem is not None:
                    sets.append("subsystem = ?")
                    params.append(subsystem)
                if sets:
                    params.append(robot_id)
                    conn.execute(
                        f"UPDATE agents SET {', '.join(sets)} WHERE robot_id = ?",
                        params,
                    )
            conn.commit()

    def record_heartbeat(
        self,
        *,
        robot_id: str,
        buffer_size: int | None = None,
        agent_url: str | None = None,
        agent_version: str | None = None,
    ) -> None:
        """Update agents.last_heartbeat and append a row to agent_heartbeats.
        Auto-creates the agent row if first heartbeat. agent_url + agent_version
        are updated only when provided (so heartbeats stay cheap).
        """
        # Ensure agent row exists (no-op if already there).
        self.upsert_agent(
            robot_id=robot_id,
            agent_url=agent_url,
            agent_version=agent_version,
        )
        ts = now_ms()
        with self.connect() as conn:
            conn.execute(
                "UPDATE agents SET last_heartbeat = ? WHERE robot_id = ?",
                (ts, robot_id),
            )
            conn.execute(
                """
                INSERT INTO agent_heartbeats (robot_id, heartbeat_at, buffer_size)
                VALUES (?, ?, ?)
                """,
                (robot_id, ts, buffer_size),
            )
            conn.commit()

    def get_agent(self, robot_id: str) -> AgentRow | None:
        with self.connect() as conn:
            r = conn.execute(
                "SELECT * FROM agents WHERE robot_id = ?", (robot_id,)
            ).fetchone()
            return AgentRow.from_row(r) if r else None

    def list_agents(self) -> list[AgentRow]:
        with self.connect() as conn:
            cur = conn.execute("SELECT * FROM agents ORDER BY robot_id")
            return [AgentRow.from_row(r) for r in cur.fetchall()]

    def prune_old_heartbeats(self, before_ms: int) -> int:
        """Delete heartbeats older than before_ms. Returns rows deleted.
        Called by a periodic task in Phase 2."""
        with self.connect() as conn:
            cur = conn.execute(
                "DELETE FROM agent_heartbeats WHERE heartbeat_at < ?",
                (before_ms,),
            )
            conn.commit()
            return cur.rowcount

    # ---- v2 P3.5.6 resolutions --------------------------------------

    def get_resolution(self, session_id: str) -> ResolutionRow | None:
        with self.connect() as conn:
            r = conn.execute(
                "SELECT * FROM session_resolutions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return ResolutionRow.from_row(r) if r else None

    def upsert_resolution(
        self,
        *,
        session_id: str,
        status: str,
        root_cause: str | None = None,
        linked_ticket: str | None = None,
        duplicate_of: str | None = None,
        edited_by: str | None = None,
    ) -> ResolutionRow:
        """Set the resolution for a session. resolved_at is auto-managed:
        set to now_ms() on first transition into a terminal status; cleared
        if status moves back to open/investigating. Preserves resolved_at
        on terminal→terminal transitions (e.g. resolved → duplicate keeps
        the original timestamp — MTTR measures time-to-first-resolution,
        not time-to-final-classification)."""
        if status not in RESOLUTION_STATUSES:
            raise ValueError(f"unknown status: {status!r}")
        now = now_ms()
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT status, resolved_at FROM session_resolutions WHERE session_id = ?",
                (session_id,),
            ).fetchone()

            if status in TERMINAL_STATUSES:
                if existing and existing["status"] in TERMINAL_STATUSES:
                    resolved_at = existing["resolved_at"]  # preserve original
                else:
                    resolved_at = now
            else:
                resolved_at = None

            conn.execute(
                """
                INSERT INTO session_resolutions
                  (session_id, status, root_cause, linked_ticket, duplicate_of,
                   resolved_at, edited_by, edited_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                  status = excluded.status,
                  root_cause = excluded.root_cause,
                  linked_ticket = excluded.linked_ticket,
                  duplicate_of = excluded.duplicate_of,
                  resolved_at = excluded.resolved_at,
                  edited_by = excluded.edited_by,
                  edited_at = excluded.edited_at
                """,
                (session_id, status, root_cause, linked_ticket, duplicate_of,
                 resolved_at, edited_by, now),
            )
            conn.commit()
            r = conn.execute(
                "SELECT * FROM session_resolutions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return ResolutionRow.from_row(r)

    def delete_resolution(self, session_id: str) -> bool:
        """Revert a session to the implicit 'open' state by dropping its row."""
        with self.connect() as conn:
            cur = conn.execute(
                "DELETE FROM session_resolutions WHERE session_id = ?", (session_id,)
            )
            conn.commit()
            return cur.rowcount > 0

    def list_sessions_in_window(
        self,
        *,
        started_at_gte: int,
        started_at_lt: int,
    ) -> list["FleetSessionRow"]:
        """Sessions started within `[started_at_gte, started_at_lt)` joined
        with their resolution status (if any). Powers the v2 P3.5.6b fleet
        stats endpoint — one query covers captures + resolution rollups
        + MTTR + per-pattern aggregation.
        """
        with self.connect() as conn:
            cur = conn.execute(
                """
                SELECT
                  s.id, s.robot_id, s.label, s.started_at,
                  s.subsystem, s.summary,
                  r.status AS res_status,
                  r.resolved_at AS res_resolved_at,
                  r.duplicate_of AS res_duplicate_of
                FROM sessions s
                LEFT JOIN session_resolutions r ON r.session_id = s.id
                WHERE s.started_at >= ? AND s.started_at < ?
                ORDER BY s.started_at ASC
                """,
                (started_at_gte, started_at_lt),
            )
            return [FleetSessionRow.from_row(r) for r in cur.fetchall()]

    # ---- retention --------------------------------------------------

    def total_mcap_bytes(self) -> int:
        with self.connect() as conn:
            cur = conn.execute("SELECT COALESCE(SUM(mcap_size_bytes), 0) AS n FROM sessions")
            return int(cur.fetchone()["n"])

    def list_oldest_sessions(self, limit: int) -> list[SessionRow]:
        with self.connect() as conn:
            cur = conn.execute(
                "SELECT * FROM sessions ORDER BY started_at ASC LIMIT ?",
                (limit,),
            )
            return [SessionRow.from_row(r) for r in cur.fetchall()]

    def delete_session(self, session_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.commit()
            return cur.rowcount > 0

    def known_paths(self) -> set[str]:
        with self.connect() as conn:
            cur = conn.execute("SELECT mcap_path FROM sessions")
            return {r["mcap_path"] for r in cur.fetchall()}


def now_ms() -> int:
    return int(time.time() * 1000)
