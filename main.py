"""
main.py
Entry point for the WhatsApp Group Manager system.
Starts the webhook server and scheduler together.

Usage:
    python main.py              # Start everything (webhook + scheduler)
    python main.py --scrape     # Run scraper once manually
    python main.py --add        # Run one add cycle manually
    python main.py --setup-db   # Create all database tables
    python main.py --setup-webhooks  # Register webhook URL with all instances
    python main.py --status     # Print system status
"""

import sys
import os
import threading
import time

# ── make sure core is importable ─────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.config import load_config
from core.database import run_schema, get_cursor, commit
from core.logger import setup_logger
from core import api_client

logger = setup_logger("main")


# ── setup functions ───────────────────────────────────────────────────────────

def setup_database():
    """Run schema.sql to create all tables."""
    schema_path = os.path.join(os.path.dirname(__file__), "db", "schema.sql")
    logger.info("Applying database schema...")
    run_schema(schema_path)
    logger.info("Database setup complete.")


def setup_webhooks():
    """Register webhook URL with all active instances in Evolution API."""
    cfg = load_config()
    webhook_cfg = cfg["webhook"]
    port        = webhook_cfg["listen_port"]
    endpoint    = webhook_cfg["endpoint"]

    # You need a public URL for this to work (ngrok or VPS)
    # For local testing, use ngrok: ngrok http 5000
    webhook_url = f"http://localhost:{port}{endpoint}"

    logger.warning(
        f"Webhook URL is set to {webhook_url}. "
        "For production, replace localhost with your public IP or ngrok URL."
    )

    all_instances = (
        [s["name"] for s in cfg["instances"]["scrapers"] if s.get("active")] +
        [a["name"] for a in cfg["instances"]["adders"] if a.get("active")]
    )

    for instance in all_instances:
        success = api_client.set_webhook(instance, webhook_url)
        if success:
            logger.info(f"✅ Webhook set for [{instance}]")
        else:
            logger.error(f"❌ Failed to set webhook for [{instance}]")


def print_status():
    """Print current system status from DB."""
    cur = get_cursor()

    print("\n" + "="*55)
    print("  WhatsApp Group Manager — System Status")
    print("="*55)

    # Members
    cur.execute("SELECT COUNT(*) FROM members")
    total_members = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM group_members WHERE status = 'new_scraped'")
    pending = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM group_members WHERE status = 'active'")
    active = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM group_members WHERE status = 'left'")
    left = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM group_members WHERE status = 'removed'")
    removed = cur.fetchone()[0]

    print(f"\n  Members:")
    print(f"    Total in DB       : {total_members}")
    print(f"    Pending (to add)  : {pending}")
    print(f"    Active in group   : {active}")
    print(f"    Left group        : {left}")
    print(f"    Removed from group: {removed}")

    # Instances
    cur.execute("""
        SELECT instance_name, role, adds_done, is_available, cooldown_until
        FROM instance_usage ORDER BY role, instance_name
    """)
    rows = cur.fetchall()

    print(f"\n  Instances:")
    for row in rows:
        name, role, adds_done, available, cooldown = row
        if available:
            status_str = f"✅ available ({adds_done} adds done)"
        else:
            cd_str = cooldown.strftime("%H:%M %d-%b") if cooldown else "unknown"
            status_str = f"⏸  on cooldown until {cd_str}"
        print(f"    [{name}] ({role}) — {status_str}")

    # Last scrape
    cur.execute("""
        SELECT g.name, ss.completed_at, ss.new_members, ss.total_scraped
        FROM scrape_sessions ss
        JOIN groups g ON g.id = ss.group_id
        WHERE ss.status = 'completed'
        ORDER BY ss.completed_at DESC
        LIMIT 3
    """)
    sessions = cur.fetchall()
    if sessions:
        print(f"\n  Last Scrape Sessions:")
        for s in sessions:
            name, completed, new_m, total = s
            completed_str = completed.strftime("%Y-%m-%d %H:%M") if completed else "N/A"
            print(f"    {name}: {new_m} new / {total} total — at {completed_str}")

    print("\n" + "="*55 + "\n")


# ── main run ──────────────────────────────────────────────────────────────────

def run_all():
    """Start webhook server + scheduler together."""
    from scheduler import start_scheduler
    from webhook import start_webhook

    logger.info("Starting WhatsApp Group Manager...")

    # Start scheduler in background thread
    scheduler = start_scheduler()

    # Run immediate add cycle on startup
    from adder import run_add_cycle
    logger.info("Running initial add cycle...")
    try:
        run_add_cycle()
    except Exception as e:
        logger.error(f"Initial add cycle failed: {e}")

    # Start webhook server (blocks main thread)
    logger.info("Starting webhook server (blocks here — Ctrl+C to stop)...")
    try:
        start_webhook()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        scheduler.shutdown()


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]

    if "--setup-db" in args:
        setup_database()

    elif "--setup-webhooks" in args:
        setup_webhooks()

    elif "--scrape" in args:
        logger.info("Running manual scrape...")
        from scraper import run_all_scrapes
        run_all_scrapes()

    elif "--add" in args:
        logger.info("Running manual add cycle...")
        from adder import run_add_cycle
        run_add_cycle()

    elif "--status" in args:
        print_status()

    else:
        run_all()
