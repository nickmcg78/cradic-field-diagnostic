"""Technician notes: store to Postgres (Neon) and email the technical manager
via Resend. Kept separate from app.py so the store/email logic is unit-testable.

All config comes from os.environ (never hard-coded, never committed):
  DATABASE_URL   Neon Postgres connection string (store target)
  RESEND_API_KEY / NOTES_FROM / NOTES_TO   Resend email config (send target)

Every function degrades gracefully when config is absent so a note is never
silently lost: storage failures raise (the caller returns 503/500), and email
failures return (False, reason) so the note is still stored.
"""
import os
from datetime import datetime, timezone, timedelta

import requests

# Australian Eastern Standard Time (UTC+10) — brief specifies AEST for the
# email timestamp. (No DST handling; AEST as stated.)
_AEST = timezone(timedelta(hours=10))

_table_ready = False

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS tech_notes (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT now(),
    tech_name TEXT NOT NULL,
    customer TEXT,
    machine TEXT,
    serial TEXT,
    note_text TEXT NOT NULL,
    emailed_ok BOOLEAN DEFAULT FALSE
)
"""


def database_configured() -> bool:
    return bool(os.environ.get("DATABASE_URL"))


def _connect():
    """Open a new Postgres connection. Raises if DATABASE_URL is unset or the
    psycopg driver is unavailable."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set")
    import psycopg  # psycopg 3 (psycopg[binary]); imported lazily
    return psycopg.connect(url)


def _ensure_table(conn):
    global _table_ready
    if _table_ready:
        return
    with conn.cursor() as cur:
        cur.execute(_CREATE_TABLE)
    conn.commit()
    _table_ready = True


def store_note(tech_name, customer, machine, serial, note_text):
    """Insert a note row (creating the table on first use). Returns the new id.
    Raises on any DB error — the caller must surface that, never swallow it."""
    conn = _connect()
    try:
        _ensure_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tech_notes (tech_name, customer, machine, serial, note_text) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (tech_name, customer, machine, serial, note_text),
            )
            note_id = cur.fetchone()[0]
        conn.commit()
        return note_id
    finally:
        conn.close()


def mark_emailed(note_id):
    """Flag a stored note as successfully emailed. Best-effort: raises on error
    so the caller can log it, but the note itself is already safely stored."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE tech_notes SET emailed_ok = TRUE WHERE id = %s", (note_id,))
        conn.commit()
    finally:
        conn.close()


def send_note_email(tech_name, customer, machine, serial, note_text, created_at=None):
    """Send the note to the technical manager via Resend.

    Returns (ok: bool, reason: str). Never raises for missing config — returns
    (False, reason) instead, so the caller can still report emailed=false with a
    clear reason. Network/HTTP errors also come back as (False, reason)."""
    api_key = os.environ.get("RESEND_API_KEY")
    notes_from = os.environ.get("NOTES_FROM")
    notes_to = os.environ.get("NOTES_TO")

    missing = [name for name, val in (
        ("RESEND_API_KEY", api_key),
        ("NOTES_FROM", notes_from),
        ("NOTES_TO", notes_to),
    ) if not val]
    if missing:
        return False, "email not configured (missing " + ", ".join(missing) + ")"

    ts = (created_at or datetime.now(timezone.utc)).astimezone(_AEST).strftime(
        "%Y-%m-%d %H:%M AEST"
    )
    subject = f"Field note — {customer or '—'} — {machine or '—'} — {tech_name}"
    body = (
        f"Customer:   {customer or '—'}\n"
        f"Machine:    {machine or '—'}\n"
        f"Serial:     {serial or '—'}\n"
        f"Technician: {tech_name}\n"
        f"Time:       {ts}\n"
        f"\n"
        f"{note_text}\n"
    )

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": f"Cradic Field Diagnostic <{notes_from}>",
                "to": [notes_to],
                "subject": subject,
                "text": body,
            },
            timeout=15,
        )
    except requests.RequestException as e:
        return False, f"email send failed: {e}"

    if resp.status_code >= 400:
        return False, f"resend error {resp.status_code}: {resp.text[:200]}"
    return True, "sent"
