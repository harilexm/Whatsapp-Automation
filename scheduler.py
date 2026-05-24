"""
scheduler.py
Runs the scraper daily and the adder every N minutes using APScheduler.
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import time

from core.config import load_config
from core.logger import setup_logger
from scraper import run_all_scrapes
from adder import run_add_cycle

logger = setup_logger("scheduler")


def start_scheduler():
    cfg = load_config()
    scrape_cfg = cfg["scrape_schedule"]
    add_cfg    = cfg["add_schedule"]

    scheduler = BackgroundScheduler()

    # ── Daily scraper job ─────────────────────────────────────────────────────
    if scrape_cfg.get("enabled", True):
        scheduler.add_job(
            func=run_all_scrapes,
            trigger=CronTrigger(
                hour=scrape_cfg["run_time_hour"],
                minute=scrape_cfg["run_time_minute"]
            ),
            id="daily_scraper",
            name="Daily Group Scraper",
            replace_existing=True,
            misfire_grace_time=300
        )
        logger.info(
            f"Scraper scheduled daily at "
            f"{scrape_cfg['run_time_hour']:02d}:{scrape_cfg['run_time_minute']:02d}"
        )

    # ── Adder job every N minutes ─────────────────────────────────────────────
    if add_cfg.get("enabled", True):
        interval_minutes = add_cfg.get("run_every_minutes", 20)
        scheduler.add_job(
            func=run_add_cycle,
            trigger=IntervalTrigger(minutes=interval_minutes),
            id="member_adder",
            name="Member Adder",
            replace_existing=True,
            misfire_grace_time=60
        )
        logger.info(f"Adder scheduled every {interval_minutes} minutes")

    scheduler.start()
    logger.info("Scheduler started. Running jobs in background.")
    return scheduler


if __name__ == "__main__":
    scheduler = start_scheduler()
    logger.info("Scheduler is live. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        scheduler.shutdown()
        logger.info("Scheduler stopped.")
