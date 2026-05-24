"""
webhook.py
FastAPI server that receives events from Evolution API.
Handles group participant changes: join, leave, remove, promote, demote.
Run this 24/7 alongside the scheduler.
"""

from fastapi import FastAPI, Request, HTTPException
from contextlib import asynccontextmanager
import uvicorn

from core.config import load_config
from core.database import get_cursor, commit
from core.logger import setup_logger

logger = setup_logger("webhook")
app = FastAPI(title="WA Group Manager Webhook")


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_member_id(phone: str) -> int | None:
    cur = get_cursor()
    cur.execute("SELECT id FROM members WHERE phone_number = %s", (phone,))
    row = cur.fetchone()
    return row[0] if row else None


def _get_group_id(group_wa_id: str) -> int | None:
    cur = get_cursor()
    cur.execute("SELECT id FROM groups WHERE group_wa_id = %s", (group_wa_id,))
    row = cur.fetchone()
    return row[0] if row else None


def _update_member_status(member_id: int, group_id: int, status: str, reason: str = ""):
    cur = get_cursor()
    cur.execute("""
        UPDATE group_members
        SET status = %s, status_changed_at = NOW(), change_reason = %s
        WHERE member_id = %s AND group_id = %s
    """, (status, reason, member_id, group_id))
    commit()


def _log_event(member_id: int, group_id: int, event_type: str,
               source: str = "webhook", notes: str = ""):
    cur = get_cursor()
    cur.execute("""
        INSERT INTO member_events
            (member_id, group_id, event_type, source, notes, created_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
    """, (member_id, group_id, event_type, source, notes))
    commit()


def _upsert_member_from_webhook(phone: str) -> int:
    """Insert member if not exists, return member_id."""
    cur = get_cursor()
    cur.execute("SELECT id FROM members WHERE phone_number = %s", (phone,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("""
        INSERT INTO members (phone_number, country_code, first_seen_at, updated_at)
        VALUES (%s, %s, NOW(), NOW()) RETURNING id
    """, (phone, "+" + phone[:2]))
    member_id = cur.fetchone()[0]
    commit()
    return member_id


def _upsert_group_from_webhook(group_wa_id: str) -> int:
    """Insert group if not exists, return group_id."""
    cur = get_cursor()
    cur.execute("SELECT id FROM groups WHERE group_wa_id = %s", (group_wa_id,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("""
        INSERT INTO groups (group_wa_id, name, group_type, is_active)
        VALUES (%s, 'Unknown Group', 'my_group', true) RETURNING id
    """, (group_wa_id,))
    group_id = cur.fetchone()[0]
    commit()
    return group_id


# ── action handler ────────────────────────────────────────────────────────────

def handle_participant_update(group_jid: str, participants: list, action: str):
    """
    action values from Evolution API / Baileys:
      add      → someone joined or was added
      remove   → admin kicked them
      leave    → they left on their own
      promote  → made admin
      demote   → removed from admin
    """
    group_id = _upsert_group_from_webhook(group_jid)

    action_map = {
        "add":     ("active",  "joined"),
        "remove":  ("removed", "kicked"),
        "leave":   ("left",    "left"),
        "promote": ("active",  "promoted"),
        "demote":  ("active",  "demoted"),
    }

    if action not in action_map:
        logger.warning(f"Unknown action: {action}")
        return

    status, event_type = action_map[action]

    for participant_jid in participants:
        phone = participant_jid.replace("@s.whatsapp.net", "").strip()
        if not phone:
            continue

        member_id = _upsert_member_from_webhook(phone)

        # Upsert group_members row
        cur = get_cursor()
        cur.execute("""
            INSERT INTO group_members (member_id, group_id, status, status_changed_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (member_id, group_id) DO UPDATE
                SET status = %s, status_changed_at = NOW()
        """, (member_id, group_id, status, status))
        commit()

        # Log the event
        _log_event(member_id, group_id, event_type, "webhook",
                   f"Action '{action}' received from webhook")

        logger.info(f"[{action.upper()}] {phone} in group {group_jid} → status={status}")


# ── routes ─────────────────────────────────────────────────────────────────────

@app.get("/")
async def health():
    return {"status": "ok", "service": "WA Group Manager Webhook"}


@app.post("/webhook/events")
async def receive_event(request: Request):
    """Main webhook endpoint. Evolution API POSTs all events here."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = payload.get("event", "")
    data  = payload.get("data", {})

    logger.debug(f"Received event: {event}")

    if event == "group.participants.update" or event == "GROUP_PARTICIPANTS_UPDATE":
        group_jid    = data.get("id", "")
        participants = data.get("participants", [])
        action       = data.get("action", "")

        if group_jid and participants and action:
            handle_participant_update(group_jid, participants, action)
        else:
            logger.warning(f"Incomplete group update payload: {data}")

    elif event == "connection.update" or event == "CONNECTION_UPDATE":
        instance = payload.get("instance", "unknown")
        state    = data.get("state", "")
        logger.info(f"Connection update for [{instance}]: {state}")

    else:
        logger.debug(f"Unhandled event type: {event}")

    return {"received": True}


# ── run ───────────────────────────────────────────────────────────────────────

def start_webhook():
    cfg = load_config()
    port = cfg["webhook"]["listen_port"]
    logger.info(f"Starting webhook server on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    start_webhook()
