"""SQLite storage for Heard.

Entities:
  profile      - a person (member, seeker, and/or overcomer) OR a church
  entry        - one logged struggle / journal entry
  commitment   - "I will not do X tonight", with a scheduled follow-up
  outcome      - what actually happened when the follow-up was answered
  connection   - a request between two people, gated on mutual acceptance
  message      - text exchanged after a connection is accepted
  run          - a stored agent trace (token counts) for the write-up
"""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  handle TEXT NOT NULL,
  city TEXT,
  tradition TEXT,
  tradition_other TEXT,
  is_seeker INTEGER DEFAULT 0,
  beliefs TEXT,
  bio TEXT,
  overcame TEXT,
  fighting TEXT,
  open_to_contact INTEGER DEFAULT 0,
  contact_method TEXT,
  contact_value TEXT,
  synthetic INTEGER DEFAULT 0,
  service_times TEXT,
  address TEXT,
  website TEXT,
  created REAL
);
CREATE TABLE IF NOT EXISTS entry (
  id TEXT PRIMARY KEY, profile_id TEXT, text TEXT, theme TEXT,
  severity TEXT, intensity INTEGER, created REAL
);
CREATE TABLE IF NOT EXISTS commitment (
  id TEXT PRIMARY KEY, profile_id TEXT, entry_id TEXT, text TEXT,
  due REAL, created REAL, status TEXT DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS outcome (
  id TEXT PRIMARY KEY, commitment_id TEXT, profile_id TEXT,
  result TEXT, note TEXT, created REAL
);
CREATE TABLE IF NOT EXISTS connection (
  id TEXT PRIMARY KEY, from_id TEXT, to_id TEXT, theme TEXT,
  status TEXT DEFAULT 'pending', created REAL, decided REAL
);
CREATE TABLE IF NOT EXISTS message (
  id TEXT PRIMARY KEY, connection_id TEXT, from_id TEXT, body TEXT, created REAL
);
CREATE TABLE IF NOT EXISTS run (
  id TEXT PRIMARY KEY, profile_id TEXT, entry_id TEXT, label TEXT,
  trace TEXT, created REAL
);
CREATE TABLE IF NOT EXISTS chat (
  id TEXT PRIMARY KEY, profile_id TEXT, role TEXT, content TEXT,
  meta TEXT, created REAL
);
CREATE TABLE IF NOT EXISTS checkin (
  id TEXT PRIMARY KEY, profile_id TEXT, theme TEXT,
  pull INTEGER, acted TEXT, direction TEXT, note TEXT,
  trend TEXT, reply TEXT, used_model INTEGER DEFAULT 0, created REAL
);
CREATE INDEX IF NOT EXISTS idx_chat_profile ON chat(profile_id, created);
CREATE INDEX IF NOT EXISTS idx_checkin_profile ON checkin(profile_id, theme, created);
CREATE INDEX IF NOT EXISTS idx_entry_profile ON entry(profile_id);
CREATE INDEX IF NOT EXISTS idx_commit_profile ON commitment(profile_id, status);
CREATE INDEX IF NOT EXISTS idx_conn_to ON connection(to_id, status);
"""


@contextmanager
def db():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    with db() as c:
        c.executescript(SCHEMA)
        # Columns added after the first schema. SQLite has no ADD COLUMN IF
        # NOT EXISTS, so check first; existing databases keep their data.
        cols = {r["name"] for r in c.execute("PRAGMA table_info(profile)").fetchall()}
        if "checkin_hours" not in cols:
            c.execute("ALTER TABLE profile ADD COLUMN checkin_hours INTEGER DEFAULT 24")
        if "onboarded" not in cols:
            c.execute("ALTER TABLE profile ADD COLUMN onboarded INTEGER DEFAULT 0")


# ------------------------------------------------------------------- chat
def add_chat(profile_id, role, content, meta=None):
    mid = nid()
    with db() as c:
        c.execute("INSERT INTO chat VALUES (?,?,?,?,?,?)",
                  (mid, profile_id, role, content, json.dumps(meta or {}), time.time()))
    return mid


def chat_for(profile_id, limit=60):
    with db() as c:
        rs = c.execute("SELECT * FROM chat WHERE profile_id=? ORDER BY created DESC LIMIT ?",
                       (profile_id, limit)).fetchall()
    out = []
    for r in reversed(rs):
        d = dict(r)
        d["meta"] = json.loads(d["meta"] or "{}")
        out.append(d)
    return out


def clear_chat(profile_id):
    with db() as c:
        c.execute("DELETE FROM chat WHERE profile_id=?", (profile_id,))


# --------------------------------------------------------------- check-ins
def add_checkin(profile_id, theme, pull, acted, direction, note, trend, reply, used_model):
    cid = nid()
    with db() as c:
        c.execute("INSERT INTO checkin VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (cid, profile_id, theme, pull, acted, direction, note, trend, reply,
                   1 if used_model else 0, time.time()))
    return cid


def checkins_for(profile_id, theme=None, limit=60):
    q, a = "SELECT * FROM checkin WHERE profile_id=?", [profile_id]
    if theme:
        q += " AND theme=?"
        a.append(theme)
    with db() as c:
        rs = c.execute(q + " ORDER BY created DESC LIMIT ?", a + [limit]).fetchall()
    return [dict(r) for r in rs]


def checkin_stats(profile_id=None):
    q, a = "SELECT trend, COUNT(*) n FROM checkin", []
    if profile_id:
        q += " WHERE profile_id=?"
        a = [profile_id]
    with db() as c:
        rows = c.execute(q + " GROUP BY trend", a).fetchall()
    d = {r["trend"]: r["n"] for r in rows}
    return {"counts": d, "total": sum(d.values())}


def nid():
    return uuid.uuid4().hex[:12]


def _loads(row, *fields):
    d = dict(row)
    for f in fields:
        if d.get(f):
            try:
                d[f] = json.loads(d[f])
            except (json.JSONDecodeError, TypeError):
                d[f] = {} if f == "beliefs" else []
        else:
            d[f] = {} if f == "beliefs" else []
    return d


# ---------------------------------------------------------------- profiles
def create_profile(**kw):
    pid = kw.pop("id", None) or nid()
    for f in ("beliefs", "overcame", "fighting"):
        if f in kw and not isinstance(kw[f], str):
            kw[f] = json.dumps(kw[f])
    cols = ["id", "created"] + list(kw)
    vals = [pid, time.time()] + list(kw.values())
    placeholders = ",".join(["?"] * len(cols))
    with db() as c:
        c.execute(
            "INSERT OR REPLACE INTO profile (" + ",".join(cols) + ") VALUES (" + placeholders + ")",
            vals,
        )
    return pid


def get_profile(pid):
    with db() as c:
        r = c.execute("SELECT * FROM profile WHERE id=?", (pid,)).fetchone()
    return _loads(r, "beliefs", "overcame", "fighting") if r else None


def all_profiles(kind="person"):
    with db() as c:
        rs = c.execute("SELECT * FROM profile WHERE kind=?", (kind,)).fetchall()
    return [_loads(r, "beliefs", "overcame", "fighting") for r in rs]


# ---------------------------------------------------------------- entries
def add_entry(profile_id, text, theme, severity, intensity):
    eid = nid()
    with db() as c:
        c.execute(
            "INSERT INTO entry VALUES (?,?,?,?,?,?,?)",
            (eid, profile_id, text, theme, severity, intensity, time.time()),
        )
    return eid


def entries_for(profile_id, limit=50):
    with db() as c:
        rs = c.execute(
            "SELECT * FROM entry WHERE profile_id=? ORDER BY created DESC LIMIT ?",
            (profile_id, limit),
        ).fetchall()
    return [dict(r) for r in rs]


# ------------------------------------------------------------ commitments
def add_commitment(profile_id, entry_id, text, due_minutes=180):
    cid = nid()
    with db() as c:
        c.execute(
            "INSERT INTO commitment (id,profile_id,entry_id,text,due,created,status) "
            "VALUES (?,?,?,?,?,?,'open')",
            (cid, profile_id, entry_id, text, time.time() + due_minutes * 60, time.time()),
        )
    return cid


def commitments_for(profile_id, status=None):
    q = "SELECT * FROM commitment WHERE profile_id=?"
    a = [profile_id]
    if status:
        q += " AND status=?"
        a.append(status)
    with db() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY created DESC", a).fetchall()]


def due_commitments(profile_id):
    with db() as c:
        rs = c.execute(
            "SELECT * FROM commitment WHERE profile_id=? AND status='open' "
            "AND due<=? ORDER BY due",
            (profile_id, time.time()),
        ).fetchall()
    return [dict(r) for r in rs]


def record_outcome(commitment_id, profile_id, result, note=""):
    oid = nid()
    with db() as c:
        c.execute(
            "INSERT INTO outcome VALUES (?,?,?,?,?,?)",
            (oid, commitment_id, profile_id, result, note, time.time()),
        )
        c.execute("UPDATE commitment SET status='closed' WHERE id=?", (commitment_id,))
    return oid


def outcomes_for(profile_id):
    with db() as c:
        rs = c.execute(
            "SELECT o.*, c.text AS commitment_text FROM outcome o "
            "LEFT JOIN commitment c ON c.id=o.commitment_id "
            "WHERE o.profile_id=? ORDER BY o.created DESC",
            (profile_id,),
        ).fetchall()
    return [dict(r) for r in rs]


def outcome_stats(profile_id=None):
    q = "SELECT result, COUNT(*) n FROM outcome"
    a = []
    if profile_id:
        q += " WHERE profile_id=?"
        a = [profile_id]
    with db() as c:
        rows = c.execute(q + " GROUP BY result", a).fetchall()
    d = {r["result"]: r["n"] for r in rows}
    total = sum(d.values())
    return {
        "counts": d,
        "total": total,
        "held_rate": round(d.get("held", 0) / total, 3) if total else None,
    }


# ----------------------------------------------------------- connections
def request_connection(from_id, to_id, theme):
    cid = nid()
    with db() as c:
        c.execute(
            "INSERT INTO connection (id,from_id,to_id,theme,status,created) "
            "VALUES (?,?,?,?,'pending',?)",
            (cid, from_id, to_id, theme, time.time()),
        )
    return cid


def decide_connection(cid, status):
    with db() as c:
        c.execute(
            "UPDATE connection SET status=?, decided=? WHERE id=?",
            (status, time.time(), cid),
        )


def connections_for(pid):
    with db() as c:
        rs = c.execute(
            "SELECT * FROM connection WHERE from_id=? OR to_id=? ORDER BY created DESC",
            (pid, pid),
        ).fetchall()
    return [dict(r) for r in rs]


def connection(cid):
    with db() as c:
        r = c.execute("SELECT * FROM connection WHERE id=?", (cid,)).fetchone()
    return dict(r) if r else None


def add_message(connection_id, from_id, body):
    mid = nid()
    with db() as c:
        c.execute(
            "INSERT INTO message VALUES (?,?,?,?,?)",
            (mid, connection_id, from_id, body, time.time()),
        )
    return mid


def messages_for(connection_id):
    with db() as c:
        rs = c.execute(
            "SELECT * FROM message WHERE connection_id=? ORDER BY created",
            (connection_id,),
        ).fetchall()
    return [dict(r) for r in rs]


# ---------------------------------------------------------------- traces
def save_run(profile_id, entry_id, label, trace):
    rid = nid()
    with db() as c:
        c.execute(
            "INSERT INTO run VALUES (?,?,?,?,?,?)",
            (rid, profile_id, entry_id, label, json.dumps(trace), time.time()),
        )
    return rid


def all_runs(limit=200):
    with db() as c:
        rs = c.execute("SELECT * FROM run ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rs:
        d = dict(r)
        d["trace"] = json.loads(d["trace"])
        out.append(d)
    return out
