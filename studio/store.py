"""SQLite persistence for projects, image slots, chat history, events and videos.

Deliberately stdlib-only (``sqlite3``) so the dashboard has no ORM dependency on
a 24/7 VPS. Every function opens a short-lived connection, which makes the store
safe to call from both the asyncio request handlers and the job worker threads.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id            TEXT PRIMARY KEY,
    slug          TEXT NOT NULL,
    brief         TEXT NOT NULL,
    duration      INTEGER NOT NULL DEFAULT 60,
    notes         TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'planning',
    image_count   INTEGER NOT NULL DEFAULT 0,
    analysis      TEXT NOT NULL DEFAULT '',
    plan          TEXT NOT NULL DEFAULT '',
    index_html    TEXT NOT NULL DEFAULT '',
    error         TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS slots (
    project_id   TEXT NOT NULL,
    idx          INTEGER NOT NULL,
    filename     TEXT NOT NULL,
    scene        TEXT NOT NULL DEFAULT '',
    content      TEXT NOT NULL DEFAULT '',
    background   TEXT NOT NULL DEFAULT '',
    aspect       TEXT NOT NULL DEFAULT '',
    resolution   TEXT NOT NULL DEFAULT '',
    prompt       TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'empty',
    orig_name    TEXT NOT NULL DEFAULT '',
    bytes        INTEGER NOT NULL DEFAULT 0,
    uploaded_at  REAL,
    PRIMARY KEY (project_id, idx)
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  TEXT NOT NULL,
    ts          REAL NOT NULL,
    type        TEXT NOT NULL,
    data        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS videos (
    id           TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL DEFAULT '',
    filename     TEXT NOT NULL,
    path         TEXT NOT NULL,
    bytes        INTEGER NOT NULL DEFAULT 0,
    duration     REAL NOT NULL DEFAULT 0,
    width        INTEGER NOT NULL DEFAULT 0,
    height       INTEGER NOT NULL DEFAULT 0,
    brief        TEXT NOT NULL DEFAULT '',
    slug         TEXT NOT NULL DEFAULT '',
    created_at   REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_project ON events (project_id, id);
CREATE INDEX IF NOT EXISTS idx_messages_project ON messages (project_id, id);
"""

_write_lock = threading.Lock()
_local = threading.local()


def _connect() -> sqlite3.Connection:
    """One connection per thread.

    The dashboard writes from request handlers *and* from pipeline job threads
    at the same time. A single shared connection lets one thread commit or reset
    a statement another thread is mid-way through, so each thread gets its own
    handle to the same WAL database instead.
    """
    conn = getattr(_local, "conn", None)
    if conn is None:
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(settings.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        _local.conn = conn
    return conn


def init() -> None:
    conn = _connect()
    with _write_lock:
        conn.executescript(_SCHEMA)
        conn.commit()


def query(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    cur = _connect().execute(sql, tuple(params))
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    return rows


def query_one(sql: str, params: Iterable[Any] = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    conn = _connect()
    with _write_lock:
        cur = conn.execute(sql, tuple(params))
        conn.commit()
        rowid = cur.lastrowid
        cur.close()
        return rowid


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now() -> float:
    return time.time()


# ── Projects ────────────────────────────────────────────────────────────────

def create_project(brief: str, slug: str, duration: int, notes: str = "") -> dict:
    now_ts = now()
    pid = new_id("prj")
    execute(
        """INSERT INTO projects (id, slug, brief, duration, notes, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 'planning', ?, ?)""",
        (pid, slug, brief, duration, notes, now_ts, now_ts),
    )
    return get_project(pid)  # type: ignore[return-value]


def get_project(pid: str) -> dict | None:
    return query_one("SELECT * FROM projects WHERE id = ?", (pid,))


def list_projects(limit: int = 100) -> list[dict]:
    return query("SELECT * FROM projects ORDER BY created_at DESC LIMIT ?", (limit,))


def update_project(pid: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    execute(f"UPDATE projects SET {cols} WHERE id = ?", (*fields.values(), pid))


def delete_project(pid: str) -> None:
    execute("DELETE FROM events WHERE project_id = ?", (pid,))
    execute("DELETE FROM messages WHERE project_id = ?", (pid,))
    execute("DELETE FROM slots WHERE project_id = ?", (pid,))
    execute("DELETE FROM videos WHERE project_id = ?", (pid,))
    execute("DELETE FROM projects WHERE id = ?", (pid,))


# ── Slots ───────────────────────────────────────────────────────────────────

def replace_slots(pid: str, slots: list[dict]) -> None:
    execute("DELETE FROM slots WHERE project_id = ?", (pid,))
    rows = [
        (
            pid,
            s["idx"],
            s["filename"],
            s.get("scene", ""),
            s.get("content", ""),
            s.get("background", ""),
            s.get("aspect", ""),
            s.get("resolution", ""),
            s.get("prompt", ""),
            "empty",
            "",
            0,
            None,
        )
        for s in slots
    ]
    with _write_lock:
        conn = _connect()
        conn.executemany(
            """INSERT INTO slots
               (project_id, idx, filename, scene, content, background, aspect, resolution,
                prompt, status, orig_name, bytes, uploaded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()


def get_slots(pid: str) -> list[dict]:
    return query("SELECT * FROM slots WHERE project_id = ? ORDER BY idx", (pid,))


def get_slot(pid: str, idx: int) -> dict | None:
    return query_one("SELECT * FROM slots WHERE project_id = ? AND idx = ?", (pid, idx))


def mark_slot_filled(pid: str, idx: int, orig_name: str, size: int) -> None:
    execute(
        """UPDATE slots SET status = 'ready', orig_name = ?, bytes = ?, uploaded_at = ?
           WHERE project_id = ? AND idx = ?""",
        (orig_name, size, now(), pid, idx),
    )


def clear_slot(pid: str, idx: int) -> None:
    execute(
        "UPDATE slots SET status = 'empty', orig_name = '', bytes = 0, uploaded_at = NULL WHERE project_id = ? AND idx = ?",
        (pid, idx),
    )


# ── Chat history (LLM context across phases) ────────────────────────────────

def add_message(pid: str, role: str, content: str) -> None:
    execute(
        "INSERT INTO messages (project_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (pid, role, content, now()),
    )


def get_messages(pid: str) -> list[dict]:
    return query("SELECT role, content FROM messages WHERE project_id = ? ORDER BY id", (pid,))


def clear_messages(pid: str) -> None:
    execute("DELETE FROM messages WHERE project_id = ?", (pid,))


# ── Events (replayed then live-streamed over SSE) ───────────────────────────

def add_event(pid: str, type_: str, data: dict) -> dict:
    ts = now()
    payload = {"type": type_, "ts": ts, "data": data}
    eid = execute(
        "INSERT INTO events (project_id, ts, type, data) VALUES (?, ?, ?, ?)",
        (pid, ts, type_, json.dumps(data)),
    )
    payload["id"] = eid
    return payload


def get_events(pid: str, after_id: int = 0, limit: int = 4000) -> list[dict]:
    rows = query(
        "SELECT id, ts, type, data FROM events WHERE project_id = ? AND id > ? ORDER BY id LIMIT ?",
        (pid, after_id, limit),
    )
    for r in rows:
        try:
            r["data"] = json.loads(r["data"])
        except (TypeError, ValueError):
            r["data"] = {}
    return rows


def trim_events(pid: str, keep: int = 3000) -> None:
    total = query_one("SELECT COUNT(*) AS c FROM events WHERE project_id = ?", (pid,))
    if not total or total["c"] <= keep:
        return
    cutoff = query_one(
        "SELECT id FROM events WHERE project_id = ? ORDER BY id DESC LIMIT 1 OFFSET ?",
        (pid, keep),
    )
    if cutoff:
        execute("DELETE FROM events WHERE project_id = ? AND id < ?", (pid, cutoff["id"]))


# ── Videos (history) ────────────────────────────────────────────────────────

def add_video(**fields: Any) -> str:
    vid = fields.get("id") or new_id("vid")
    execute(
        """INSERT INTO videos (id, project_id, filename, path, bytes, duration, width, height,
                               brief, slug, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            vid,
            fields.get("project_id", ""),
            fields["filename"],
            str(fields["path"]),
            int(fields.get("bytes", 0)),
            float(fields.get("duration", 0)),
            int(fields.get("width", 0)),
            int(fields.get("height", 0)),
            fields.get("brief", ""),
            fields.get("slug", ""),
            fields.get("created_at", now()),
        ),
    )
    return vid


def get_video(vid: str) -> dict | None:
    return query_one("SELECT * FROM videos WHERE id = ?", (vid,))


def list_videos(limit: int = 200) -> list[dict]:
    return query("SELECT * FROM videos ORDER BY created_at DESC LIMIT ?", (limit,))


def delete_video(vid: str) -> None:
    execute("DELETE FROM videos WHERE id = ?", (vid,))


def import_existing_renders(known_paths: set[str], on_probe) -> list[str]:
    """Register videos already sitting in renders/ but never rendered here.

    ``on_probe(path) -> (width, height, duration)`` is injected so this module
    stays free of ffprobe/process concerns.
    """
    from config import settings as cfg

    added: list[str] = []
    if not cfg.renders_dir.exists():
        return added
    for path in sorted(cfg.renders_dir.glob("*.mp4"), reverse=True):
        key = str(path.resolve())
        if key in known_paths:
            continue
        try:
            width, height, duration = on_probe(path)
        except Exception:  # noqa: BLE001 - a bad file must not break the scan
            width, height, duration = 0, 0, 0.0
        slug = path.stem.rsplit("_", 2)[0] if path.stem.count("_") >= 2 else path.stem
        add_video(
            project_id="",
            filename=path.name,
            path=key,
            bytes=path.stat().st_size,
            duration=duration,
            width=width,
            height=height,
            brief=slug.replace("-", " ").replace("_", " "),
            slug=slug,
            created_at=path.stat().st_mtime,
        )
        known_paths.add(key)
        added.append(path.name)
    return added


def refresh_video_paths() -> None:
    """Drop history rows whose file has been removed from disk."""
    for row in query("SELECT id, path FROM videos"):
        if not Path(row["path"]).exists():
            delete_video(row["id"])