"""SQLite storage for contacts, intake threads, intake items and AI case drafts.

SQLite keeps the pilot free and dependency-free. Every query lives here, so moving
to Postgres/Supabase later means changing this one file.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from .config import settings

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY,
    address TEXT NOT NULL UNIQUE,          -- whatsapp:+237..., +237..., kobo:..., upload:...
    display_name TEXT,
    channel TEXT NOT NULL,                 -- whatsapp | sms | call | kobo | upload | agent
    consent_status TEXT NOT NULL DEFAULT 'pending',  -- pending | notified | granted | opted_out
    language TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS threads (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'open',   -- open | draft_ready | approved | rejected
    title TEXT,
    opened_at TEXT NOT NULL,
    last_item_at TEXT NOT NULL,
    stale_summary INTEGER NOT NULL DEFAULT 0   -- 1 when items arrived after the latest draft
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    thread_id INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,                 -- whatsapp | sms | call | kobo | upload | agent
    direction TEXT NOT NULL DEFAULT 'in',  -- in (from school/partner) | out (from PS43 staff)
    kind TEXT NOT NULL,                    -- text | audio | image | video | document | form
    author TEXT,                           -- sender name when known
    body TEXT,                             -- original text, caption or form content
    media_path TEXT,
    media_mime TEXT,
    external_id TEXT UNIQUE,               -- Twilio MessageSid/CallSid, Kobo uuid... (dedupe)
    status TEXT NOT NULL DEFAULT 'received', -- received | awaiting_media | held | processing | processed | failed
    extracted_text TEXT,                   -- transcript / image description / form text
    extraction_meta TEXT,                  -- JSON: language, duration, model...
    error TEXT,
    sent_at TEXT NOT NULL,                 -- when the person sent it
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS summaries (
    id INTEGER PRIMARY KEY,
    thread_id INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    model TEXT,
    data TEXT NOT NULL,                    -- JSON case draft (SNAP)
    status TEXT NOT NULL DEFAULT 'draft',  -- draft | approved | rejected | superseded
    item_ids TEXT NOT NULL,                -- JSON list of item ids the draft was built from
    reviewer TEXT,
    review_notes TEXT,
    case_number TEXT,
    confirmation_sent_at TEXT,
    created_at TEXT NOT NULL,
    reviewed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_items_thread ON items(thread_id);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_threads_contact ON threads(contact_id);
CREATE INDEX IF NOT EXISTS idx_summaries_thread ON summaries(thread_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    settings.ensure_dirs()
    conn = sqlite3.connect(settings.db_path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """A short transaction. Serialised with a process-wide lock (SQLite + threads)."""
    with _lock:
        conn = _connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db() -> None:
    with tx() as conn:
        conn.executescript(SCHEMA)


def row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(r) if r is not None else None


# ---------------------------------------------------------------- contacts

def get_or_create_contact(address: str, channel: str, display_name: str | None = None) -> dict[str, Any]:
    with tx() as conn:
        found = conn.execute("SELECT * FROM contacts WHERE address = ?", (address,)).fetchone()
        if found:
            if display_name and not found["display_name"]:
                conn.execute("UPDATE contacts SET display_name = ?, updated_at = ? WHERE id = ?", (display_name, now(), found["id"]))
                found = conn.execute("SELECT * FROM contacts WHERE id = ?", (found["id"],)).fetchone()
            return dict(found)
        ts = now()
        cur = conn.execute(
            "INSERT INTO contacts (address, display_name, channel, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (address, display_name, channel, ts, ts),
        )
        return dict(conn.execute("SELECT * FROM contacts WHERE id = ?", (cur.lastrowid,)).fetchone())


def get_contact(contact_id: int) -> dict[str, Any] | None:
    with tx() as conn:
        return row(conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone())


def set_consent(contact_id: int, status: str) -> None:
    with tx() as conn:
        conn.execute("UPDATE contacts SET consent_status = ?, updated_at = ? WHERE id = ?", (status, now(), contact_id))


def update_contact(contact_id: int, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with tx() as conn:
        conn.execute(f"UPDATE contacts SET {cols}, updated_at = ? WHERE id = ?", (*fields.values(), now(), contact_id))


def delete_contact_data(contact_id: int) -> list[str]:
    """Opt-out: delete everything stored about a contact. Returns media paths to remove."""
    with tx() as conn:
        paths = [r["media_path"] for r in conn.execute(
            "SELECT media_path FROM items WHERE contact_id = ? AND media_path IS NOT NULL", (contact_id,))]
        conn.execute("DELETE FROM threads WHERE contact_id = ?", (contact_id,))
        conn.execute("DELETE FROM items WHERE contact_id = ?", (contact_id,))
        return paths


# ---------------------------------------------------------------- threads

def open_thread_for(contact_id: int, at: str | None = None) -> dict[str, Any]:
    """Return the contact's active intake thread, or start a new one.

    Messages keep landing in the same thread until staff approve/reject it, or the
    contact goes quiet for THREAD_IDLE_HOURS.
    """
    at = at or now()
    cutoff = (datetime.fromisoformat(at) - timedelta(hours=settings.thread_idle_hours)).isoformat(timespec="seconds")
    with tx() as conn:
        t = conn.execute(
            "SELECT * FROM threads WHERE contact_id = ? AND status IN ('open', 'draft_ready') "
            "ORDER BY id DESC LIMIT 1",
            (contact_id,),
        ).fetchone()
        if t and t["last_item_at"] >= cutoff:
            return dict(t)
        cur = conn.execute(
            "INSERT INTO threads (contact_id, opened_at, last_item_at) VALUES (?, ?, ?)", (contact_id, at, at)
        )
        return dict(conn.execute("SELECT * FROM threads WHERE id = ?", (cur.lastrowid,)).fetchone())


def touch_thread(thread_id: int, at: str) -> None:
    with tx() as conn:
        t = conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
        if not t:
            return
        stale = 1 if t["status"] == "draft_ready" else t["stale_summary"]
        conn.execute(
            "UPDATE threads SET last_item_at = MAX(last_item_at, ?), stale_summary = ? WHERE id = ?",
            (at, stale, thread_id),
        )


def get_thread(thread_id: int) -> dict[str, Any] | None:
    with tx() as conn:
        return row(conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone())


def set_thread(thread_id: int, **fields: Any) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    with tx() as conn:
        conn.execute(f"UPDATE threads SET {cols} WHERE id = ?", (*fields.values(), thread_id))


def list_threads(status: str | None = None) -> list[dict[str, Any]]:
    q = (
        "SELECT t.*, c.display_name, c.address, c.channel AS contact_channel, c.consent_status, "
        "(SELECT COUNT(*) FROM items i WHERE i.thread_id = t.id) AS item_count, "
        "(SELECT COUNT(*) FROM items i WHERE i.thread_id = t.id AND i.status IN ('received','processing','awaiting_media')) AS pending_count, "
        "(SELECT COUNT(*) FROM items i WHERE i.thread_id = t.id AND i.status = 'failed') AS failed_count "
        "FROM threads t JOIN contacts c ON c.id = t.contact_id "
    )
    args: tuple = ()
    if status:
        q += "WHERE t.status = ? "
        args = (status,)
    q += "ORDER BY t.last_item_at DESC"
    with tx() as conn:
        return [dict(r) for r in conn.execute(q, args)]


# ---------------------------------------------------------------- items

def add_item(
    *,
    contact_id: int,
    channel: str,
    kind: str,
    body: str | None = None,
    media_path: str | None = None,
    media_mime: str | None = None,
    external_id: str | None = None,
    direction: str = "in",
    author: str | None = None,
    status: str = "received",
    sent_at: str | None = None,
    thread_id: int | None = None,
) -> dict[str, Any] | None:
    """Store one inbound item. Returns None if this external_id was already stored (webhook retry)."""
    sent_at = sent_at or now()
    if external_id:
        with tx() as conn:
            if conn.execute("SELECT 1 FROM items WHERE external_id = ?", (external_id,)).fetchone():
                return None
    if thread_id is None:
        thread_id = open_thread_for(contact_id, sent_at)["id"]
    with tx() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO items (thread_id, contact_id, channel, direction, kind, author, body, media_path, "
                "media_mime, external_id, status, sent_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (thread_id, contact_id, channel, direction, kind, author, body, media_path, media_mime,
                 external_id, status, sent_at, now()),
            )
        except sqlite3.IntegrityError:
            return None
        item = dict(conn.execute("SELECT * FROM items WHERE id = ?", (cur.lastrowid,)).fetchone())
    touch_thread(thread_id, sent_at)
    return item


def get_item(item_id: int) -> dict[str, Any] | None:
    with tx() as conn:
        return row(conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone())


def get_item_by_external_id(external_id: str) -> dict[str, Any] | None:
    with tx() as conn:
        return row(conn.execute("SELECT * FROM items WHERE external_id = ?", (external_id,)).fetchone())


def update_item(item_id: int, **fields: Any) -> None:
    if "extraction_meta" in fields and not isinstance(fields["extraction_meta"], (str, type(None))):
        fields["extraction_meta"] = json.dumps(fields["extraction_meta"], ensure_ascii=False)
    cols = ", ".join(f"{k} = ?" for k in fields)
    with tx() as conn:
        conn.execute(f"UPDATE items SET {cols} WHERE id = ?", (*fields.values(), item_id))


def thread_items(thread_id: int) -> list[dict[str, Any]]:
    with tx() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM items WHERE thread_id = ? ORDER BY sent_at, id", (thread_id,))]


def items_with_status(*statuses: str) -> list[dict[str, Any]]:
    marks = ",".join("?" for _ in statuses)
    with tx() as conn:
        return [dict(r) for r in conn.execute(f"SELECT * FROM items WHERE status IN ({marks}) ORDER BY id", statuses)]


def release_held_items(contact_id: int) -> list[dict[str, Any]]:
    with tx() as conn:
        held = [dict(r) for r in conn.execute(
            "SELECT * FROM items WHERE contact_id = ? AND status = 'held'", (contact_id,))]
        conn.execute("UPDATE items SET status = 'received' WHERE contact_id = ? AND status = 'held'", (contact_id,))
    return held


# ---------------------------------------------------------------- summaries

def add_summary(thread_id: int, data: dict, model: str, item_ids: list[int]) -> dict[str, Any]:
    with tx() as conn:
        conn.execute("UPDATE summaries SET status = 'superseded' WHERE thread_id = ? AND status = 'draft'", (thread_id,))
        version = conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM summaries WHERE thread_id = ?", (thread_id,)).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO summaries (thread_id, version, model, data, item_ids, created_at) VALUES (?,?,?,?,?,?)",
            (thread_id, version, model, json.dumps(data, ensure_ascii=False), json.dumps(item_ids), now()),
        )
        conn.execute("UPDATE threads SET status = 'draft_ready', stale_summary = 0 WHERE id = ?", (thread_id,))
        return dict(conn.execute("SELECT * FROM summaries WHERE id = ?", (cur.lastrowid,)).fetchone())


def latest_summary(thread_id: int) -> dict[str, Any] | None:
    with tx() as conn:
        s = conn.execute(
            "SELECT * FROM summaries WHERE thread_id = ? AND status != 'superseded' ORDER BY version DESC LIMIT 1",
            (thread_id,),
        ).fetchone()
        return decode_summary(s)


def get_summary(summary_id: int) -> dict[str, Any] | None:
    with tx() as conn:
        return decode_summary(conn.execute("SELECT * FROM summaries WHERE id = ?", (summary_id,)).fetchone())


def decode_summary(s: sqlite3.Row | None) -> dict[str, Any] | None:
    if s is None:
        return None
    d = dict(s)
    d["data"] = json.loads(d["data"])
    d["item_ids"] = json.loads(d["item_ids"])
    return d


def update_summary(summary_id: int, **fields: Any) -> None:
    if "data" in fields and not isinstance(fields["data"], str):
        fields["data"] = json.dumps(fields["data"], ensure_ascii=False)
    cols = ", ".join(f"{k} = ?" for k in fields)
    with tx() as conn:
        conn.execute(f"UPDATE summaries SET {cols} WHERE id = ?", (*fields.values(), summary_id))


def next_case_number() -> str:
    year = datetime.now(timezone.utc).year
    prefix = f"PS43-{year}-"
    with tx() as conn:
        last = conn.execute(
            "SELECT case_number FROM summaries WHERE case_number LIKE ? ORDER BY case_number DESC LIMIT 1",
            (prefix + "%",),
        ).fetchone()
    n = int(last["case_number"].rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{n:04d}"
