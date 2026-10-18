"""SQLite storage for contacts, intake threads, intake items and AI case drafts.

SQLite keeps the pilot free and dependency-free. Every query lives here, so moving
to Postgres/Supabase later means changing this one file.
"""
from __future__ import annotations

import json
import re
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
    reply_to TEXT,                         -- last address we can message them on (whatsapp:+... or +...)
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS threads (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'open',   -- open | draft_ready | approved | resolved | not_relevant (all still collecting) | closed | rejected
    relevance_locked INTEGER NOT NULL DEFAULT 0, -- 1 = staff decided relevance; the AI no longer changes it
    case_number TEXT UNIQUE,               -- PS43-2026-0001, given when the case is opened
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
    external_id TEXT UNIQUE,               -- WhatsApp Desktop message id, Kobo uuid... (dedupe)
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

# Part two: donors, tickets (one per school need), deployment steps, message log, follow-ups.
OPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS donors (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER UNIQUE REFERENCES contacts(id) ON DELETE CASCADE,
    thread_id INTEGER REFERENCES threads(id) ON DELETE SET NULL,   -- latest approved offer case
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'unknown',  -- individual | diaspora_group | company | ngo | foundation | faith_group | government | other
    gives TEXT NOT NULL DEFAULT '[]',      -- JSON: money, goods, services, volunteers, transport, expertise
    regions TEXT NOT NULL DEFAULT '[]',    -- JSON list as stated ("Northwest", "National"...)
    summary TEXT,                          -- what they can provide, in plain words
    amount TEXT,
    timeframe TEXT,
    conditions TEXT,
    registered TEXT NOT NULL DEFAULT 'unknown',
    past_work_note TEXT,                   -- earlier work with PS43 as they described it
    roles TEXT NOT NULL DEFAULT '[]',      -- JSON: donor, implementer, supplier, shipper
    verified INTEGER NOT NULL DEFAULT 0,   -- staff checked who they are (registration, references)
    verified_by TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS donor_items (
    id INTEGER PRIMARY KEY,
    donor_id INTEGER NOT NULL REFERENCES donors(id) ON DELETE CASCADE,
    source_thread_id INTEGER,
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    quantity_text TEXT,
    quantity_total REAL,                   -- number parsed from quantity_text (400 sheets -> 400)
    unit TEXT,
    availability TEXT,
    conditions TEXT,
    location TEXT,                         -- where the items are now
    abroad INTEGER NOT NULL DEFAULT 0,     -- 1 = outside Cameroon, needs shipping
    status TEXT NOT NULL DEFAULT 'available',  -- available | withdrawn
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY,
    thread_id INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    need_key TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    quantity_text TEXT,
    quantity_total REAL,
    unit TEXT,
    urgency TEXT NOT NULL DEFAULT 'unknown',
    status TEXT NOT NULL DEFAULT 'new',    -- new | matched | in_progress | delivered | checked | cancelled
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    delivered_at TEXT,
    UNIQUE (thread_id, need_key)
);

CREATE TABLE IF NOT EXISTS ticket_links (     -- donor commitments = the donor's history of cases with PS43
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    donor_id INTEGER NOT NULL REFERENCES donors(id) ON DELETE CASCADE,
    donor_item_id INTEGER REFERENCES donor_items(id) ON DELETE SET NULL,
    quantity REAL,
    status TEXT NOT NULL DEFAULT 'committed',  -- committed | delivered | completed | cancelled
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ticket_steps (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    title TEXT NOT NULL,
    owner TEXT NOT NULL DEFAULT 'ps43',    -- ps43 | partner | school
    due TEXT,
    done_at TEXT,
    done_by TEXT
);

CREATE TABLE IF NOT EXISTS ticket_events (    -- the communication and activity log
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    at TEXT NOT NULL,
    who TEXT,
    kind TEXT NOT NULL,                    -- created | status | match | message | note | step | followup
    text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS followups (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    days INTEGER NOT NULL,                 -- 30 | 90 | 180 after delivery
    due_on TEXT NOT NULL,                  -- YYYY-MM-DD
    sent_at TEXT,
    sent_by TEXT,
    UNIQUE (ticket_id, days)
);

CREATE TABLE IF NOT EXISTS quotes (            -- prices from contractors/suppliers, checked against each other
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    donor_id INTEGER REFERENCES donors(id) ON DELETE SET NULL,
    supplier_name TEXT,                    -- when the quote is from someone not in the database
    amount REAL NOT NULL,                  -- total, in FCFA
    quantity REAL,
    unit TEXT,
    note TEXT,
    chosen INTEGER NOT NULL DEFAULT 0,
    added_by TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (     -- staff-editable settings (scoring weights, unit costs, demo flag)
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tickets_thread ON tickets(thread_id);
CREATE INDEX IF NOT EXISTS idx_links_ticket ON ticket_links(ticket_id);
CREATE INDEX IF NOT EXISTS idx_links_donor ON ticket_links(donor_id);
CREATE INDEX IF NOT EXISTS idx_items_donor ON donor_items(donor_id);
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
        conn.executescript(OPS_SCHEMA)
        _migrate(conn)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _migrate(conn: sqlite3.Connection) -> None:
    """Upgrade databases made by earlier versions in place (no data lost)."""
    if "case_number" not in _columns(conn, "threads"):
        conn.execute("ALTER TABLE threads ADD COLUMN case_number TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_threads_case ON threads(case_number)")
    if "relevance_locked" not in _columns(conn, "threads"):
        conn.execute("ALTER TABLE threads ADD COLUMN relevance_locked INTEGER NOT NULL DEFAULT 0")
    if "avatar_path" not in _columns(conn, "contacts"):
        conn.execute("ALTER TABLE contacts ADD COLUMN avatar_path TEXT")  # WhatsApp profile photo
    if "logo_path" not in _columns(conn, "donors"):
        conn.execute("ALTER TABLE donors ADD COLUMN logo_path TEXT")      # partner logo uploaded by staff
    if "resolved_at" not in _columns(conn, "threads"):
        conn.execute("ALTER TABLE threads ADD COLUMN resolved_at TEXT")
    if "reply_to" not in _columns(conn, "contacts"):
        conn.execute("ALTER TABLE contacts ADD COLUMN reply_to TEXT")
    # 1. one contact per phone number: whatsapp:+237..., +237... and tel:+237... are the same person
    for cid in [r["id"] for r in conn.execute("SELECT id FROM contacts ORDER BY id")]:
        c = conn.execute("SELECT * FROM contacts WHERE id = ?", (cid,)).fetchone()
        if c is None:
            continue  # merged into another contact earlier in this loop
        canon = canonical_address(c["address"])
        reply = c["reply_to"] or (c["address"] if is_messageable(c["address"]) else None)
        if canon == c["address"]:
            if reply != c["reply_to"]:
                conn.execute("UPDATE contacts SET reply_to = ? WHERE id = ?", (reply, c["id"]))
            continue
        keeper = conn.execute("SELECT * FROM contacts WHERE address = ?", (canon,)).fetchone()
        if keeper:
            _merge_contacts(conn, keep_id=keeper["id"], drop_id=c["id"], reply_to=reply)
        else:
            conn.execute("UPDATE contacts SET address = ?, reply_to = ? WHERE id = ?", (canon, reply, c["id"]))
    # 2. tidy names saved from WhatsApp window titles ("\u200eConnor - WhatsApp voice call")
    for c in conn.execute("SELECT id, display_name FROM contacts WHERE display_name IS NOT NULL").fetchall():
        tidy = clean_name(c["display_name"])
        if tidy != c["display_name"]:
            conn.execute("UPDATE contacts SET display_name = ? WHERE id = ?", (tidy, c["id"]))
    # 3. every case gets a case number (older versions only numbered approved drafts)
    for t in conn.execute("SELECT id FROM threads WHERE case_number IS NULL ORDER BY id").fetchall():
        old = conn.execute("SELECT case_number FROM summaries WHERE thread_id = ? AND case_number IS NOT NULL "
                           "ORDER BY id DESC LIMIT 1", (t["id"],)).fetchone()
        number = old["case_number"] if old else _next_case_number(conn)
        conn.execute("UPDATE threads SET case_number = ? WHERE id = ?", (number, t["id"]))


def _merge_contacts(conn: sqlite3.Connection, keep_id: int, drop_id: int, reply_to: str | None = None) -> None:
    conn.execute("UPDATE items SET contact_id = ? WHERE contact_id = ?", (keep_id, drop_id))
    conn.execute("UPDATE threads SET contact_id = ? WHERE contact_id = ?", (keep_id, drop_id))
    drop = conn.execute("SELECT * FROM contacts WHERE id = ?", (drop_id,)).fetchone()
    conn.execute("UPDATE contacts SET display_name = COALESCE(display_name, ?), language = COALESCE(language, ?), "
                 "reply_to = COALESCE(?, reply_to) WHERE id = ?",
                 (drop["display_name"], drop["language"], reply_to, keep_id))
    conn.execute("DELETE FROM contacts WHERE id = ?", (drop_id,))


# ---------------------------------------------------------------- addresses

_PHONE = re.compile(r"^\+?\d{7,15}$")


def phone_of(address: str | None) -> str | None:
    """'whatsapp:+237 670-000-001' -> '+237670000001'; None if the address is not a phone number."""
    if not address:
        return None
    raw = address.split(":", 1)[1] if address.lower().startswith(("whatsapp:", "tel:", "sms:")) else address
    digits = re.sub(r"[\s().-]", "", raw)
    if _PHONE.match(digits):
        return "+" + digits.lstrip("+")
    return None


_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\ufeff"))
_CALL_SUFFIX = re.compile(r"\s*[-–—|·:]?\s*(whatsapp\s+(voice|video|audio)\s+call|(voice|video)\s+call|"
                          r"appel\s+(vocal|vidéo|video)(\s+whatsapp)?|whatsapp)\s*$", re.I)


def clean_name(name: str | None) -> str | None:
    """'\u200eConnor - WhatsApp voice call' -> 'Connor' (WhatsApp window titles carry such extras)."""
    if not name:
        return None
    n = name.translate(_INVISIBLE).strip()
    for _ in range(2):
        n = _CALL_SUFFIX.sub("", n).strip(" -–—|·:")
    return n or None


def canonical_address(address: str) -> str:
    """One identity per person: a phone number, whatever channel it arrived on."""
    return phone_of(address) or address


def is_messageable(address: str | None) -> bool:
    return bool(address) and (address.startswith("whatsapp:") or bool(_PHONE.match(address or "")))


def row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(r) if r is not None else None


# ---------------------------------------------------------------- contacts

def get_or_create_contact(address: str, channel: str, display_name: str | None = None) -> dict[str, Any]:
    """Find the person behind an address on any channel (WhatsApp, SMS, call, KoBo...) or create them.

    Phone numbers are the identity: a WhatsApp message, an SMS and a call from +237... all land on the
    same contact. Addresses without a number (an uploaded chat with only a name) fall back to the name.
    """
    display_name = clean_name(display_name)
    canon = canonical_address(address)
    # Only a message they sent us tells us how to reply; a number typed by staff or on a form
    # is used for replies only if we have nothing better.
    reply = address if is_messageable(address) else None
    reply_is_inbound = channel in ("whatsapp", "sms")
    with tx() as conn:
        found = conn.execute("SELECT * FROM contacts WHERE address = ?", (canon,)).fetchone()
        if not found and display_name and not phone_of(canon):
            # no number (e.g. a WhatsApp Desktop call): match anyone with that name
            found = conn.execute(
                "SELECT * FROM contacts WHERE lower(display_name) = lower(?) ORDER BY updated_at DESC LIMIT 1",
                (display_name.strip(),)).fetchone()
        if not found and display_name and phone_of(canon):
            # first message with a number from someone we only knew by name (their call came in first):
            # give that contact the number, so the text joins the same case
            nameless = conn.execute(
                "SELECT * FROM contacts WHERE lower(display_name) = lower(?) AND address NOT LIKE '+%' "
                "ORDER BY updated_at DESC LIMIT 1", (display_name.strip(),)).fetchone()
            if nameless:
                conn.execute("UPDATE contacts SET address = ?, updated_at = ? WHERE id = ?",
                             (canon, now(), nameless["id"]))
                found = conn.execute("SELECT * FROM contacts WHERE id = ?", (nameless["id"],)).fetchone()
        if found:
            updates: dict[str, Any] = {}
            if display_name and not found["display_name"]:
                updates["display_name"] = display_name
            if reply and reply != found["reply_to"] and (reply_is_inbound or not found["reply_to"]):
                updates["reply_to"] = reply
            if updates:
                cols = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(f"UPDATE contacts SET {cols}, updated_at = ? WHERE id = ?",
                             (*updates.values(), now(), found["id"]))
                found = conn.execute("SELECT * FROM contacts WHERE id = ?", (found["id"],)).fetchone()
            return dict(found)
        ts = now()
        cur = conn.execute(
            "INSERT INTO contacts (address, display_name, channel, reply_to, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (canon, display_name, channel, reply, ts, ts),
        )
        return dict(conn.execute("SELECT * FROM contacts WHERE id = ?", (cur.lastrowid,)).fetchone())


def find_contact_by_name(name: str) -> dict[str, Any] | None:
    name = clean_name(name) or ""
    with tx() as conn:
        return row(conn.execute(
            "SELECT * FROM contacts WHERE lower(display_name) = lower(?) ORDER BY updated_at DESC LIMIT 1",
            (name.strip(),)).fetchone())


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

ACTIVE_STATUSES = ("open", "draft_ready", "approved", "resolved", "not_relevant")


def open_thread_for(contact_id: int, at: str | None = None) -> dict[str, Any]:
    """Return the contact's active case, or open a new one with its own case number.

    Everything a contact sends - texts, voice notes, photos, videos, calls, forms - goes into the
    same case until staff close (or reject) it. Approved cases keep collecting new material.
    THREAD_IDLE_HOURS > 0 additionally starts a new case after that long without contact.
    """
    at = at or now()
    with tx() as conn:
        t = conn.execute(
            "SELECT * FROM threads WHERE contact_id = ? AND status IN ('open', 'draft_ready', 'approved', 'resolved', 'not_relevant') "
            "ORDER BY id DESC LIMIT 1",
            (contact_id,),
        ).fetchone()
        if t:
            if settings.thread_idle_hours <= 0:
                return dict(t)
            cutoff = (datetime.fromisoformat(at) - timedelta(hours=settings.thread_idle_hours)).isoformat(timespec="seconds")
            if t["last_item_at"] >= cutoff:
                return dict(t)
        cur = conn.execute(
            "INSERT INTO threads (contact_id, opened_at, last_item_at, case_number) VALUES (?, ?, ?, ?)",
            (contact_id, at, at, _next_case_number(conn)),
        )
        return dict(conn.execute("SELECT * FROM threads WHERE id = ?", (cur.lastrowid,)).fetchone())


def get_thread_by_case(case_number: str) -> dict[str, Any] | None:
    with tx() as conn:
        return row(conn.execute("SELECT * FROM threads WHERE upper(case_number) = upper(?)",
                                (case_number.strip(),)).fetchone())


def move_item(item_id: int, thread_id: int) -> None:
    with tx() as conn:
        old = conn.execute("SELECT thread_id, sent_at FROM items WHERE id = ?", (item_id,)).fetchone()
        conn.execute("UPDATE items SET thread_id = ? WHERE id = ?", (thread_id, item_id))
    if old:
        touch_thread(thread_id, old["sent_at"])


def merge_threads(from_id: int, into_id: int) -> int:
    """Move every item of one case into another and remove the emptied case. Returns items moved."""
    with tx() as conn:
        n = conn.execute("UPDATE items SET thread_id = ? WHERE thread_id = ?", (into_id, from_id)).rowcount
        src = conn.execute("SELECT * FROM threads WHERE id = ?", (from_id,)).fetchone()
        conn.execute("UPDATE summaries SET status = 'superseded' WHERE thread_id = ?", (from_id,))
        conn.execute("UPDATE summaries SET thread_id = ? WHERE thread_id = ?", (into_id, from_id))
        conn.execute("DELETE FROM threads WHERE id = ?", (from_id,))
        if src:
            conn.execute("UPDATE threads SET last_item_at = MAX(last_item_at, ?), "
                         "stale_summary = CASE WHEN status IN ('draft_ready','approved') THEN 1 ELSE stale_summary END "
                         "WHERE id = ?", (src["last_item_at"], into_id))
    return n


def touch_thread(thread_id: int, at: str) -> None:
    with tx() as conn:
        t = conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
        if not t:
            return
        stale = 1 if t["status"] in ("draft_ready", "approved", "resolved") else t["stale_summary"]
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
        "SELECT t.*, c.display_name, c.address, c.channel AS contact_channel, c.consent_status, c.avatar_path, "
        "(SELECT i.id FROM items i WHERE i.thread_id = t.id AND i.kind = 'image' AND i.media_path IS NOT NULL "
        " AND i.direction = 'in' ORDER BY i.sent_at DESC, i.id DESC LIMIT 1) AS cover_item_id, "
        "(SELECT COUNT(*) FROM items i WHERE i.thread_id = t.id) AS item_count, "
        "(SELECT COUNT(*) FROM items i WHERE i.thread_id = t.id AND i.status IN ('received','processing','awaiting_media')) AS pending_count, "
        "(SELECT COUNT(*) FROM items i WHERE i.thread_id = t.id AND i.status = 'failed') AS failed_count, "
        "(SELECT GROUP_CONCAT(DISTINCT i.channel) FROM items i WHERE i.thread_id = t.id) AS channels, "
        "(SELECT GROUP_CONCAT(DISTINCT i.kind) FROM items i WHERE i.thread_id = t.id) AS kinds "
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
        # a resolved case stays resolved (it keeps counting on Impact); its new draft is flagged for review
        # staff marked it not relevant: it stays set aside (the AI cannot undo a staff decision)
        conn.execute("UPDATE threads SET status = CASE WHEN status = 'resolved' THEN 'resolved' "
                     "WHEN status = 'not_relevant' AND relevance_locked = 1 THEN 'not_relevant' ELSE 'draft_ready' END, "
                     "stale_summary = CASE WHEN status = 'resolved' THEN 1 ELSE 0 END WHERE id = ?", (thread_id,))
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


def _next_case_number(conn: sqlite3.Connection) -> str:
    prefix = f"PS43-{datetime.now(timezone.utc).year}-"
    highest = 0
    for table in ("threads", "summaries"):
        r = conn.execute(f"SELECT case_number FROM {table} WHERE case_number LIKE ? "
                         "ORDER BY case_number DESC LIMIT 1", (prefix + "%",)).fetchone()
        if r and r["case_number"]:
            highest = max(highest, int(r["case_number"].rsplit("-", 1)[1]))
    return f"{prefix}{highest + 1:04d}"



# ================================================================ part two: donors, tickets, follow-ups

def _update(table: str, row_id: int, fields: dict[str, Any]) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with tx() as conn:
        conn.execute(f"UPDATE {table} SET {cols} WHERE id = ?", (*fields.values(), row_id))


def _decode_donor(d: dict | None) -> dict | None:
    if d:
        d["gives"] = json.loads(d.get("gives") or "[]")
        d["regions"] = json.loads(d.get("regions") or "[]")
        d["roles"] = json.loads(d.get("roles") or "[]")
    return d


def upsert_donor(contact_id: int | None, thread_id: int | None, **fields: Any) -> dict[str, Any]:
    """One donor per contact. Later approved offers update the record (staff checks are kept)."""
    for k in ("gives", "regions", "roles"):
        if k in fields:
            fields[k] = json.dumps(fields[k], ensure_ascii=False)
    ts = now()
    with tx() as conn:
        existing = conn.execute("SELECT id FROM donors WHERE contact_id = ?", (contact_id,)).fetchone() \
            if contact_id is not None else None
        if existing:
            cols = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE donors SET {cols}, thread_id = COALESCE(?, thread_id), updated_at = ? WHERE id = ?",
                         (*fields.values(), thread_id, ts, existing["id"]))
            donor_id = existing["id"]
        else:
            keys = ["contact_id", "thread_id", *fields, "created_at", "updated_at"]
            cur = conn.execute(f"INSERT INTO donors ({', '.join(keys)}) VALUES ({', '.join('?' * len(keys))})",
                               (contact_id, thread_id, *fields.values(), ts, ts))
            donor_id = cur.lastrowid
        return _decode_donor(dict(conn.execute("SELECT * FROM donors WHERE id = ?", (donor_id,)).fetchone()))


def get_donor(donor_id: int) -> dict[str, Any] | None:
    with tx() as conn:
        return _decode_donor(row(conn.execute("SELECT * FROM donors WHERE id = ?", (donor_id,)).fetchone()))


def donor_for_contact(contact_id: int) -> dict[str, Any] | None:
    with tx() as conn:
        return _decode_donor(row(conn.execute("SELECT * FROM donors WHERE contact_id = ?", (contact_id,)).fetchone()))


def list_donors() -> list[dict[str, Any]]:
    with tx() as conn:
        rows = conn.execute(
            "SELECT d.*, c.address, c.reply_to, c.consent_status, c.avatar_path AS contact_avatar, t.case_number FROM donors d "
            "LEFT JOIN contacts c ON c.id = d.contact_id LEFT JOIN threads t ON t.id = d.thread_id "
            "ORDER BY d.name COLLATE NOCASE").fetchall()
        return [_decode_donor(dict(r)) for r in rows]


def set_donor(donor_id: int, **fields: Any) -> None:
    fields["updated_at"] = now()
    _update("donors", donor_id, fields)


def replace_donor_items(donor_id: int, source_thread_id: int | None, items: list[dict[str, Any]]) -> None:
    """Refresh the items that came from one offer case. Items already promised to a school are kept."""
    ts = now()
    with tx() as conn:
        conn.execute("DELETE FROM donor_items WHERE donor_id = ? AND source_thread_id IS ? AND "
                     "id NOT IN (SELECT donor_item_id FROM ticket_links WHERE donor_item_id IS NOT NULL)",
                     (donor_id, source_thread_id))
        kept = {(r["category"], r["description"].lower()) for r in conn.execute(
            "SELECT category, description FROM donor_items WHERE donor_id = ?", (donor_id,))}
        for it in items:
            if (it["category"], it["description"].lower()) in kept:
                continue
            _insert_item(conn, donor_id, source_thread_id, it, ts)


def _insert_item(conn: sqlite3.Connection, donor_id: int, source_thread_id: int | None, it: dict, ts: str) -> int:
    return conn.execute(
        "INSERT INTO donor_items (donor_id, source_thread_id, category, description, quantity_text, "
        "quantity_total, unit, availability, conditions, location, abroad, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (donor_id, source_thread_id, it["category"], it["description"], it.get("quantity_text"),
         it.get("quantity_total"), it.get("unit"), it.get("availability"), it.get("conditions"),
         it.get("location"), int(bool(it.get("abroad"))), ts)).lastrowid


def add_donor_item(donor_id: int, source_thread_id: int | None, item: dict) -> int:
    with tx() as conn:
        return _insert_item(conn, donor_id, source_thread_id, item, now())


def list_donor_items(donor_id: int | None = None) -> list[dict[str, Any]]:
    q = ("SELECT di.*, COALESCE((SELECT SUM(l.quantity) FROM ticket_links l WHERE l.donor_item_id = di.id "
         "AND l.status != 'cancelled'), 0) AS committed, "
         "(SELECT COUNT(*) FROM ticket_links l WHERE l.donor_item_id = di.id AND l.status != 'cancelled') AS link_count "
         "FROM donor_items di ")
    args: tuple = ()
    if donor_id is not None:
        q += "WHERE di.donor_id = ? "
        args = (donor_id,)
    with tx() as conn:
        return [dict(r) for r in conn.execute(q + "ORDER BY di.id", args)]


def set_donor_item(item_id: int, **fields: Any) -> None:
    _update("donor_items", item_id, fields)


# ---------------------------------------------------------------- tickets

TICKET_SELECT = ("SELECT k.*, t.case_number, t.title AS case_title, t.contact_id, c.display_name, c.address, "
                 "c.reply_to, c.consent_status, c.avatar_path FROM tickets k JOIN threads t ON t.id = k.thread_id "
                 "JOIN contacts c ON c.id = t.contact_id ")


def upsert_ticket(thread_id: int, need_key: str, **fields: Any) -> tuple[dict[str, Any], bool]:
    """Create the ticket for one school need, or refresh its text. Returns (ticket, created)."""
    ts = now()
    with tx() as conn:
        existing = conn.execute("SELECT id FROM tickets WHERE thread_id = ? AND need_key = ?",
                                (thread_id, need_key)).fetchone()
        if existing:
            cols = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE tickets SET {cols}, updated_at = ? WHERE id = ?", (*fields.values(), ts, existing["id"]))
            tid, created = existing["id"], False
        else:
            keys = ["thread_id", "need_key", *fields, "created_at", "updated_at"]
            tid = conn.execute(f"INSERT INTO tickets ({', '.join(keys)}) VALUES ({', '.join('?' * len(keys))})",
                               (thread_id, need_key, *fields.values(), ts, ts)).lastrowid
            created = True
        return dict(conn.execute(TICKET_SELECT + "WHERE k.id = ?", (tid,)).fetchone()), created


def get_ticket(ticket_id: int) -> dict[str, Any] | None:
    with tx() as conn:
        return row(conn.execute(TICKET_SELECT + "WHERE k.id = ?", (ticket_id,)).fetchone())


def list_tickets(status: str | None = None, thread_id: int | None = None) -> list[dict[str, Any]]:
    q, args = TICKET_SELECT + "WHERE 1=1 ", []
    if status:
        q += "AND k.status = ? "
        args.append(status)
    if thread_id is not None:
        q += "AND k.thread_id = ? "
        args.append(thread_id)
    with tx() as conn:
        return [dict(r) for r in conn.execute(q + "ORDER BY k.id", args)]


def set_ticket(ticket_id: int, **fields: Any) -> None:
    fields["updated_at"] = now()
    _update("tickets", ticket_id, fields)


def add_link(ticket_id: int, donor_id: int, donor_item_id: int | None, quantity: float | None) -> dict[str, Any]:
    ts = now()
    with tx() as conn:
        lid = conn.execute("INSERT INTO ticket_links (ticket_id, donor_id, donor_item_id, quantity, created_at, updated_at) "
                           "VALUES (?,?,?,?,?,?)", (ticket_id, donor_id, donor_item_id, quantity, ts, ts)).lastrowid
        return dict(conn.execute("SELECT * FROM ticket_links WHERE id = ?", (lid,)).fetchone())


def list_links(ticket_id: int | None = None, donor_id: int | None = None) -> list[dict[str, Any]]:
    q = ("SELECT l.*, d.name AS donor_name, d.contact_id AS donor_contact_id, di.description AS item_description, "
         "di.unit AS item_unit, k.category, k.description AS need_description, k.status AS ticket_status, "
         "k.thread_id, t.case_number, t.title AS case_title FROM ticket_links l JOIN donors d ON d.id = l.donor_id "
         "LEFT JOIN donor_items di ON di.id = l.donor_item_id JOIN tickets k ON k.id = l.ticket_id "
         "JOIN threads t ON t.id = k.thread_id WHERE 1=1 ")
    args: list = []
    if ticket_id is not None:
        q += "AND l.ticket_id = ? "
        args.append(ticket_id)
    if donor_id is not None:
        q += "AND l.donor_id = ? "
        args.append(donor_id)
    with tx() as conn:
        return [dict(r) for r in conn.execute(q + "ORDER BY l.id", args)]


def set_link(link_id: int, **fields: Any) -> None:
    fields["updated_at"] = now()
    _update("ticket_links", link_id, fields)


def set_links_for_ticket(ticket_id: int, status: str, only_from: tuple[str, ...]) -> None:
    marks = ",".join("?" * len(only_from))
    with tx() as conn:
        conn.execute(f"UPDATE ticket_links SET status = ?, updated_at = ? WHERE ticket_id = ? AND status IN ({marks})",
                     (status, now(), ticket_id, *only_from))


def add_steps(ticket_id: int, steps: list[tuple[str, str]]) -> None:
    with tx() as conn:
        start = conn.execute("SELECT COALESCE(MAX(position), 0) FROM ticket_steps WHERE ticket_id = ?",
                             (ticket_id,)).fetchone()[0]
        for n, (title, owner) in enumerate(steps, start + 1):
            conn.execute("INSERT INTO ticket_steps (ticket_id, position, title, owner) VALUES (?,?,?,?)",
                         (ticket_id, n, title, owner))


def insert_steps(ticket_id: int, before_position: int, steps: list[tuple[str, str]]) -> None:
    """Insert steps before an existing position (later steps move down)."""
    with tx() as conn:
        conn.execute("UPDATE ticket_steps SET position = position + ? WHERE ticket_id = ? AND position >= ?",
                     (len(steps), ticket_id, before_position))
        for n, (title, owner) in enumerate(steps):
            conn.execute("INSERT INTO ticket_steps (ticket_id, position, title, owner) VALUES (?,?,?,?)",
                         (ticket_id, before_position + n, title, owner))


def list_steps(ticket_id: int) -> list[dict[str, Any]]:
    with tx() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM ticket_steps WHERE ticket_id = ? ORDER BY position",
                                              (ticket_id,))]


def get_step(step_id: int) -> dict[str, Any] | None:
    with tx() as conn:
        return row(conn.execute("SELECT * FROM ticket_steps WHERE id = ?", (step_id,)).fetchone())


def set_step(step_id: int, **fields: Any) -> None:
    _update("ticket_steps", step_id, fields)


def add_event(ticket_id: int, kind: str, text: str, who: str | None = None, at: str | None = None) -> None:
    with tx() as conn:
        conn.execute("INSERT INTO ticket_events (ticket_id, at, who, kind, text) VALUES (?,?,?,?,?)",
                     (ticket_id, at or now(), who, kind, text))


def list_events(ticket_id: int) -> list[dict[str, Any]]:
    with tx() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM ticket_events WHERE ticket_id = ? ORDER BY at, id",
                                              (ticket_id,))]


def add_followups(ticket_id: int, delivered_on: datetime, days: tuple[int, ...] = (30, 90, 180)) -> None:
    with tx() as conn:
        for d in days:
            conn.execute("INSERT OR IGNORE INTO followups (ticket_id, days, due_on) VALUES (?,?,?)",
                         (ticket_id, d, (delivered_on + timedelta(days=d)).date().isoformat()))


def list_followups(ticket_id: int | None = None, due_by: str | None = None, unsent_only: bool = False) -> list[dict[str, Any]]:
    q = ("SELECT f.*, k.description, k.category, k.thread_id, t.case_number, t.title AS case_title, t.contact_id, "
         "c.display_name, c.reply_to, c.address FROM followups f JOIN tickets k ON k.id = f.ticket_id "
         "JOIN threads t ON t.id = k.thread_id JOIN contacts c ON c.id = t.contact_id WHERE 1=1 ")
    args: list = []
    if ticket_id is not None:
        q += "AND f.ticket_id = ? "
        args.append(ticket_id)
    if due_by:
        q += "AND f.due_on <= ? "
        args.append(due_by)
    if unsent_only:
        q += "AND f.sent_at IS NULL "
    with tx() as conn:
        return [dict(r) for r in conn.execute(q + "ORDER BY f.due_on, f.id", args)]


def get_followup(followup_id: int) -> dict[str, Any] | None:
    rows = [f for f in list_followups() if f["id"] == followup_id]
    return rows[0] if rows else None


def set_followup(followup_id: int, **fields: Any) -> None:
    _update("followups", followup_id, fields)


def latest_approved_summary(thread_id: int) -> dict[str, Any] | None:
    with tx() as conn:
        return decode_summary(conn.execute(
            "SELECT * FROM summaries WHERE thread_id = ? AND status = 'approved' ORDER BY version DESC LIMIT 1",
            (thread_id,)).fetchone())



def add_quote(ticket_id: int, amount: float, *, donor_id: int | None = None, supplier_name: str | None = None,
              quantity: float | None = None, unit: str | None = None, note: str | None = None,
              added_by: str | None = None) -> int:
    with tx() as conn:
        return conn.execute(
            "INSERT INTO quotes (ticket_id, donor_id, supplier_name, amount, quantity, unit, note, added_by, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (ticket_id, donor_id, supplier_name, amount, quantity, unit, note, added_by, now())).lastrowid


def list_quotes(ticket_id: int | None = None) -> list[dict[str, Any]]:
    q = ("SELECT q.*, COALESCE(d.name, q.supplier_name, 'Unnamed supplier') AS who, k.category, k.unit AS need_unit "
         "FROM quotes q JOIN tickets k ON k.id = q.ticket_id LEFT JOIN donors d ON d.id = q.donor_id ")
    args: tuple = ()
    if ticket_id is not None:
        q += "WHERE q.ticket_id = ? "
        args = (ticket_id,)
    with tx() as conn:
        return [dict(r) for r in conn.execute(q + "ORDER BY q.id", args)]


def choose_quote(ticket_id: int, quote_id: int) -> None:
    with tx() as conn:
        conn.execute("UPDATE quotes SET chosen = (id = ?) WHERE ticket_id = ?", (quote_id, ticket_id))



def get_setting(key: str, default: Any = None) -> Any:
    with tx() as conn:
        r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def set_setting(key: str, value: Any) -> None:
    with tx() as conn:
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, json.dumps(value)))


def activity_counts() -> dict[str, Any]:
    """Volumes used for the cost-per-case estimate on the Impact page."""
    with tx() as conn:
        one = lambda q: conn.execute(q).fetchone()[0]
        return {
            "cases": one("SELECT COUNT(*) FROM threads WHERE status NOT IN ('rejected', 'not_relevant')"),
            "ai_drafts": one("SELECT COUNT(*) FROM summaries WHERE model NOT LIKE 'demo%'") ,
            "messages": one("SELECT COUNT(*) FROM items WHERE channel IN ('whatsapp', 'sms')"),
            "first_contact": one("SELECT MIN(opened_at) FROM threads"),
        }
