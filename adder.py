"""
adder.py
Reads new_scraped members from DB and adds them to your group.
Rotates through adder instances. Each instance gets a 12-hour cooldown
after adding 30 members. Respects safe hours and skip_statuses.
"""

import time
import random
from datetime import datetime, timedelta, timezone

from core.config import load_config
from core.database import get_cursor, commit, rollback
from core.logger import setup_logger
from core import api_client

logger = setup_logger("adder")


# ── instance management ───────────────────────────────────────────────────────

def _release_expired_cooldowns():
    """Reset instances whose cooldown period has passed."""
    cur = get_cursor()
    cur.execute("""
        UPDATE instance_usage
        SET is_available = true,
            adds_done    = 0,
            cooldown_until = NULL
        WHERE role = 'adder'
          AND is_available = false
          AND cooldown_until IS NOT NULL
          AND cooldown_until <= NOW()
    """)
    commit()


def _get_available_instance() -> dict | None:
    """Return the best available adder instance (fewest adds done)."""
    _release_expired_cooldowns()
    cur = get_cursor()
    cur.execute("""
        SELECT instance_name, adds_done, cooldown_until
        FROM instance_usage
        WHERE role = 'adder'
          AND is_available = true
        ORDER BY adds_done ASC, last_add_at ASC NULLS FIRST
        LIMIT 1
    """)
    row = cur.fetchone()
    if not row:
        return None
    return {"name": row[0], "adds_done": row[1]}


def _mark_add_done(instance_name: str, daily_limit: int, cooldown_hours: int):
    """Increment add count. If limit reached, put instance in cooldown."""
    cur = get_cursor()
    cur.execute("""
        UPDATE instance_usage
        SET adds_done       = adds_done + 1,
            last_add_at     = NOW(),
            total_adds_ever = total_adds_ever + 1
        WHERE instance_name = %s
    """, (instance_name,))
    commit()

    # Check if limit reached
    cur.execute("SELECT adds_done FROM instance_usage WHERE instance_name = %s", (instance_name,))
    adds_done = cur.fetchone()[0]

    if adds_done >= daily_limit:
        cooldown_until = datetime.now() + timedelta(hours=cooldown_hours)
        cur.execute("""
            UPDATE instance_usage
            SET is_available   = false,
                cooldown_until = %s
            WHERE instance_name = %s
        """, (cooldown_until, instance_name))
        commit()
        logger.warning(
            f"⏸  {instance_name} hit limit of {daily_limit}. "
            f"Cooling down until {cooldown_until.strftime('%H:%M %d-%b')}"
        )


def _get_instance_config(instance_name: str) -> dict:
    """Get daily_limit and cooldown_hours for an adder from config."""
    cfg = load_config()
    for adder in cfg["instances"]["adders"]:
        if adder["name"] == instance_name:
            return adder
    return {"daily_limit": 30, "cooldown_hours": 12}


# ── member fetching ───────────────────────────────────────────────────────────

def _get_next_member_to_add(target_group_id: int, skip_statuses: list) -> dict | None:
    """
    Get one member from DB with status = new_scraped
    who has never left/removed/blocked from any group.
    """
    cur = get_cursor()

    # Build a subquery of phones with bad history
    skip_list = ", ".join([f"'{s}'" for s in skip_statuses])

    cur.execute(f"""
        SELECT m.id, m.phone_number
        FROM members m
        JOIN group_members gm ON gm.member_id = m.id
        WHERE gm.status = 'new_scraped'
          AND m.id NOT IN (
            SELECT DISTINCT gm2.member_id
            FROM group_members gm2
            WHERE gm2.status IN ({skip_list})
          )
          AND m.id NOT IN (
            SELECT DISTINCT gm3.member_id
            FROM group_members gm3
            WHERE gm3.group_id = %s
              AND gm3.status = 'active'
          )
        ORDER BY gm.status_changed_at ASC
        LIMIT 1
    """, (target_group_id,))

    row = cur.fetchone()
    if not row:
        return None
    return {"member_id": row[0], "phone": row[1]}


def _ensure_group_in_db(group_jid: str, group_name: str, group_type: str) -> int:
    cur = get_cursor()
    cur.execute("SELECT id FROM groups WHERE group_wa_id = %s", (group_jid,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("""
        INSERT INTO groups (group_wa_id, name, group_type, is_active)
        VALUES (%s, %s, %s, true) RETURNING id
    """, (group_jid, group_name, group_type))
    group_id = cur.fetchone()[0]
    commit()
    return group_id


def _mark_member_added(member_id: int, target_group_id: int):
    """Update group_members status to active and log the event."""
    cur = get_cursor()

    # Update all new_scraped rows for this member to active
    cur.execute("""
        UPDATE group_members
        SET status = 'active', status_changed_at = NOW()
        WHERE member_id = %s AND status = 'new_scraped'
    """, (member_id,))

    # Insert into target group as active if not already there
    cur.execute("""
        INSERT INTO group_members (member_id, group_id, status, status_changed_at)
        VALUES (%s, %s, 'active', NOW())
        ON CONFLICT (member_id, group_id) DO UPDATE
            SET status = 'active', status_changed_at = NOW()
    """, (member_id, target_group_id))

    # Log event
    cur.execute("""
        INSERT INTO member_events
            (member_id, group_id, event_type, source, notes, created_at)
        VALUES (%s, %s, 'added_to_my_group', 'system', 'Auto-added by adder', NOW())
    """, (member_id, target_group_id))

    commit()


def _mark_member_failed(member_id: int, reason: str):
    """Log a failed add attempt without changing the member status."""
    cur = get_cursor()
    cur.execute("""
        INSERT INTO member_events
            (member_id, group_id, event_type, source, notes, created_at)
        SELECT %s, group_id, 'joined', 'system', %s, NOW()
        FROM group_members WHERE member_id = %s LIMIT 1
    """, (member_id, f"ADD_FAILED: {reason}", member_id))
    commit()


# ── safe hours check ─────────────────────────────────────────────────────────

def _within_safe_hours() -> bool:
    cfg = load_config()["add_schedule"]
    hour = datetime.now().hour
    return cfg["safe_hours_start"] <= hour < cfg["safe_hours_end"]


# ── main add function ─────────────────────────────────────────────────────────

def run_add_cycle():
    """
    Runs one add cycle: picks one member, picks one instance, adds member.
    Called every X minutes by the scheduler.
    """
    cfg = load_config()

    if not _within_safe_hours():
        safe_start = cfg["add_schedule"]["safe_hours_start"]
        safe_end   = cfg["add_schedule"]["safe_hours_end"]
        logger.info(f"Outside safe hours ({safe_start}:00 - {safe_end}:00). Skipping.")
        return

    target_group_cfg = cfg["my_groups"][0]
    target_group_jid = target_group_cfg["jid"]
    target_group_name = target_group_cfg["name"]
    skip_statuses    = cfg["add_schedule"]["skip_statuses"]
    rate             = cfg["rate_limits"]

    target_group_id = _ensure_group_in_db(target_group_jid, target_group_name, "my_group")

    # Get available instance
    instance = _get_available_instance()
    if not instance:
        logger.warning("All adder instances are on cooldown. Nothing to do.")
        return

    instance_cfg = _get_instance_config(instance["name"])

    # Get next member to add
    member = _get_next_member_to_add(target_group_id, skip_statuses)
    if not member:
        logger.info("No new members available to add.")
        return

    logger.info(
        f"Adding {member['phone']} to '{target_group_name}' "
        f"via [{instance['name']}] "
        f"(adds done: {instance['adds_done']}/{instance_cfg['daily_limit']})"
    )

    # Call Evolution API
    success = api_client.add_participant(
        instance=instance["name"],
        group_jid=target_group_jid,
        phone=member["phone"]
    )

    if success:
        _mark_member_added(member["member_id"], target_group_id)
        _mark_add_done(
            instance["name"],
            instance_cfg["daily_limit"],
            instance_cfg["cooldown_hours"]
        )
        logger.info(f"✅ Successfully added {member['phone']}")
    else:
        _mark_member_failed(member["member_id"], "API returned failure")
        logger.warning(f"❌ Failed to add {member['phone']}")

    # Random delay after add
    delay = random.uniform(
        rate["add_delay_min_sec"],
        rate["add_delay_max_sec"]
    )
    logger.info(f"Waiting {delay:.1f}s...")
    time.sleep(delay)


if __name__ == "__main__":
    run_add_cycle()
