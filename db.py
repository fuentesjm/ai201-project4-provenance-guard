"""SQLite persistence + audit log for Provenance Guard (planning.md Architecture).

Milestone 3 uses the submissions and audit_log tables. The appeals table is
created now so Milestone 5 has it ready, but is not yet written to.
"""

import json
import os
import sqlite3

DB_PATH = os.environ.get("PROVENANCE_DB", "provenance.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS submissions (
                content_id     TEXT PRIMARY KEY,
                creator_id     TEXT,
                text           TEXT NOT NULL,
                s1             REAL,
                s1_json        TEXT,
                s2             REAL,
                s2_rationale   TEXT,
                p              REAL,
                confidence     REAL,
                band           TEXT,
                label_variant  TEXT,
                label_text     TEXT,
                status         TEXT NOT NULL DEFAULT 'classified',
                created_at     TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS appeals (
                appeal_id      TEXT PRIMARY KEY,
                content_id     TEXT NOT NULL,
                appellant_id   TEXT,
                reason         TEXT,
                claimed_origin TEXT,
                status         TEXT NOT NULL,
                reviewer_id    TEXT,
                note           TEXT,
                created_at     TEXT NOT NULL,
                resolved_at    TEXT,
                FOREIGN KEY (content_id) REFERENCES submissions(content_id)
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                content_id  TEXT,
                appeal_id   TEXT,
                event       TEXT NOT NULL,
                detail_json TEXT,
                ts          TEXT NOT NULL
            );
            """
        )


def insert_submission(row):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO submissions (
                content_id, creator_id, text, s1, s1_json, s2, s2_rationale,
                p, confidence, band, label_variant, label_text, status, created_at
            ) VALUES (
                :content_id, :creator_id, :text, :s1, :s1_json, :s2, :s2_rationale,
                :p, :confidence, :band, :label_variant, :label_text, :status, :created_at
            )
            """,
            row,
        )


def get_submission(content_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM submissions WHERE content_id = ?", (content_id,)
        ).fetchone()
    return dict(row) if row else None


def set_submission_status(content_id, status):
    with get_conn() as conn:
        conn.execute(
            "UPDATE submissions SET status = ? WHERE content_id = ?",
            (status, content_id),
        )


def insert_appeal(row):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO appeals (
                appeal_id, content_id, appellant_id, reason, claimed_origin,
                status, reviewer_id, note, created_at, resolved_at
            ) VALUES (
                :appeal_id, :content_id, :appellant_id, :reason, :claimed_origin,
                :status, :reviewer_id, :note, :created_at, :resolved_at
            )
            """,
            row,
        )


def append_audit(event, content_id=None, appeal_id=None, detail=None, ts=None):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO audit_log (content_id, appeal_id, event, detail_json, ts)
            VALUES (?, ?, ?, ?, ?)
            """,
            (content_id, appeal_id, event, json.dumps(detail or {}), ts),
        )


def get_log(limit=50):
    """Most-recent-first audit entries, with the structured detail merged to
    the top level so each entry reads as a flat record (planning.md /log)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT content_id, appeal_id, event, detail_json, ts FROM audit_log "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    entries = []
    for r in rows:
        detail = json.loads(r["detail_json"]) if r["detail_json"] else {}
        entries.append({
            "content_id": r["content_id"],
            "appeal_id": r["appeal_id"],
            "event": r["event"],
            "timestamp": r["ts"],
            **detail,
        })
    return entries


def get_audit(content_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT event, ts, detail_json FROM audit_log "
            "WHERE content_id = ? ORDER BY id",
            (content_id,),
        ).fetchall()
    return [
        {"event": r["event"], "ts": r["ts"], "detail": json.loads(r["detail_json"])}
        for r in rows
    ]
