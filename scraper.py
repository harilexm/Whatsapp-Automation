"""
scraper.py
Scrapes members from configured scrape_groups and saves them to the database.
Skips admins. Deduplicates by phone number. Logs every run as a scrape_session.
"""

import time
import random
from datetime import datetime, timezone

from core.config import load_config
from core.database import get_cursor, commit, rollback
from core.logger import setup_logger
from core import api_client

logger = setup_logger("scraper")


# ── helpers ──────────────────────────────────────────────────────────────────

def _ensure_group_in_db(group_wa_id: str, name: str, group_type: str) -> int:
    """Insert group if not exists, return its DB id."""
    cur = get_cursor()
    cur.execute("SELECT id FROM groups WHERE group_wa_id = %s", (group_wa_id,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("""
        INSERT INTO groups (group_wa_id, name, group_type, is_active)
        VALUES (%s, %s, %s, true) RETURNING id
    """, (group_wa_id, name, group_type))
    group_id = cur.fetchone()[0]
    commit()
    return group_id


def _upsert_member(phone: str) -> tuple[int, bool]:
    """
    Insert member if not exists.
    Returns (member_id, is_new).
    """
    cur = get_cursor()
    cur.execute("SELECT id FROM members WHERE phone_number = %s", (phone,))
    row = cur.fetchone()
    if row:
        return row[0], False

    # Derive country code from phone (first 2-3 digits)
    country_code = "+" + phone[:2]

    cur.execute("""
        INSERT INTO members (phone_number, country_code, first_seen_at, updated_at)
        VALUES (%s, %s, NOW(), NOW()) RETURNING id
    """, (phone, country_code))
    member_id = cur.fetchone()[0]
    commit()
    return member_id, True


def _member_in_group(member_id: int, group_id: int) -> bool:
    cur = get_cursor()
    cur.execute(
        "SELECT 1 FROM group_members WHERE member_id = %s AND group_id = %s",
        (member_id, group_id)
    )
    return cur.fetchone() is not None


def _add_group_member(member_id: int, group_id: int, is_duplicate: bool):
    cur = get_cursor()
    cur.execute("""
        INSERT INTO group_members
            (member_id, group_id, status, is_duplicate, status_changed_at, first_added_in_group)
        VALUES (%s, %s, 'new_scraped', %s, NOW(), %s)
        ON CONFLICT (member_id, group_id) DO NOTHING
    """, (member_id, group_id, is_duplicate, group_id))


def _log_event(member_id: int, group_id: int, event_type: str, notes: str = ""):
    cur = get_cursor()
    cur.execute("""
        INSERT INTO member_events (member_id, group_id, event_type, source, notes, created_at)
        VALUES (%s, %s, %s, 'scraper', %s, NOW())
    """, (member_id, group_id, event_type, notes))


def _start_session(group_id: int) -> int:
    cur = get_cursor()
    cur.execute("""
        INSERT INTO scrape_sessions (group_id, started_at, status)
        VALUES (%s, NOW(), 'running') RETURNING id
    """, (group_id,))
    session_id = cur.fetchone()[0]
    commit()
    return session_id


def _finish_session(session_id: int, stats: dict, status: str = "completed"):
    cur = get_cursor()
    cur.execute("""
        UPDATE scrape_sessions SET
            completed_at  = NOW(),
            status        = %s,
            total_scraped = %s,
            new_members   = %s,
            duplicates    = %s,
            admins_found  = %s,
            errors        = %s,
            error_log     = %s
        WHERE id = %s
    """, (
        status,
        stats.get("total", 0),
        stats.get("new", 0),
        stats.get("duplicates", 0),
        stats.get("admins", 0),
        stats.get("errors", 0),
        stats.get("error_log", ""),
        session_id
    ))
    # Update last_scraped_at on the group
    cur.execute("""
        UPDATE groups SET last_scraped_at = NOW()
        WHERE id = (SELECT group_id FROM scrape_sessions WHERE id = %s)
    """, (session_id,))
    commit()


# ── main scrape function ──────────────────────────────────────────────────────

def scrape_group(instance: str, group_jid: str, group_name: str) -> dict:
    """
    Scrape one group. Returns stats dict.
    """
    logger.info(f"Starting scrape: '{group_name}' ({group_jid}) via instance [{instance}]")

    group_id = _ensure_group_in_db(group_jid, group_name, "scrape_group")
    session_id = _start_session(group_id)

    stats = {"total": 0, "new": 0, "duplicates": 0, "admins": 0, "errors": 0, "error_log": ""}

    try:
        participants = api_client.get_group_participants(instance, group_jid)

        if not participants:
            logger.warning(f"No participants returned for {group_jid}")
            _finish_session(session_id, stats, "failed")
            return stats

        stats["total"] = len(participants)

        for p in participants:
            try:
                # Skip admins and owners
                if p.get("admin") in ("admin", "superadmin"):
                    stats["admins"] += 1
                    continue

                phone_raw = p.get("phoneNumber", "")
                phone = phone_raw.replace("@s.whatsapp.net", "").strip()

                if not phone or not phone.isdigit():
                    stats["errors"] += 1
                    continue

                # Upsert member
                member_id, is_new = _upsert_member(phone)
                is_duplicate = not is_new

                # Check if already in this group
                if _member_in_group(member_id, group_id):
                    stats["duplicates"] += 1
                    continue

                # Add to group_members
                _add_group_member(member_id, group_id, is_duplicate)

                # Log event
                _log_event(member_id, group_id, "scraped",
                           f"Scraped from group {group_name}")

                if is_new:
                    stats["new"] += 1
                else:
                    stats["duplicates"] += 1

            except Exception as e:
                stats["errors"] += 1
                stats["error_log"] += f"{phone_raw}: {str(e)}\n"
                rollback()
                logger.error(f"Error processing participant {p}: {e}")

        commit()
        _finish_session(session_id, stats, "completed")

        logger.info(
            f"Scrape done for '{group_name}': "
            f"total={stats['total']} new={stats['new']} "
            f"dupes={stats['duplicates']} admins={stats['admins']} "
            f"errors={stats['errors']}"
        )

    except Exception as e:
        stats["error_log"] = str(e)
        _finish_session(session_id, stats, "failed")
        logger.error(f"Scrape failed for {group_jid}: {e}")

    return stats


def run_all_scrapes():
    """
    Main entry point. Scrapes all configured scrape_groups.
    Rotates through available scraper instances.
    """
    cfg = load_config()
    scrape_groups = cfg.get("scrape_groups", [])
    scrapers = [s for s in cfg["instances"]["scrapers"] if s.get("active")]
    rate = cfg["rate_limits"]

    if not scrapers:
        logger.error("No active scraper instances configured.")
        return

    if not scrape_groups:
        logger.warning("No scrape_groups in config.")
        return

    # Sort by priority
    scrape_groups = sorted(scrape_groups, key=lambda g: g.get("priority", 99))

    for i, group in enumerate(scrape_groups):
        # Rotate scraper instances
        instance = scrapers[i % len(scrapers)]["name"]

        scrape_group(
            instance=instance,
            group_jid=group["jid"],
            group_name=group["name"]
        )

        # Delay between groups (except last)
        if i < len(scrape_groups) - 1:
            delay = random.uniform(
                rate["scrape_delay_between_groups_min_sec"],
                rate["scrape_delay_between_groups_max_sec"]
            )
            logger.info(f"Waiting {delay:.0f}s before next group...")
            time.sleep(delay)

    logger.info("All scrape groups processed.")


if __name__ == "__main__":
    run_all_scrapes()
