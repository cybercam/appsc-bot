"""APScheduler wiring for the daily 07:00 IST revision delivery."""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Bot

from services import AppState, due_cards, send_due_cards, today_in_tz

logger = logging.getLogger(__name__)

# Guards against double-runs within the same calendar day (idempotency).
_last_run_date: dict[str, str] = {}


async def deliver_daily_reviews(bot: Bot, state: AppState) -> None:
    """Find all due cards and drip them to every registered chat.

    Idempotent per day: if this fires twice on the same date (e.g. a restart
    plus the cron), the second run is skipped so no card is double-scheduled.
    """
    today = today_in_tz(state.settings.timezone)
    key = today.isoformat()
    if _last_run_date.get("daily") == key:
        logger.info("Daily delivery already ran for %s; skipping.", key)
        return

    targets = state.delivery_targets()
    if not targets:
        logger.info("No registered chats; nothing to deliver.")
        _last_run_date["daily"] = key
        return

    # One read for the whole job run (cache); writes happen on grading.
    state.repo.invalidate_cache()
    cards = state.repo.load_cards(use_cache=True)
    due = due_cards(cards, today)
    logger.info("Daily job: %d card(s) due for %d chat(s).", len(due), len(targets))

    if not due:
        # Silent when nothing is due, per spec.
        _last_run_date["daily"] = key
        return

    for chat_id in targets:
        try:
            await send_due_cards(bot, chat_id, due)
        except Exception:  # noqa: BLE001
            logger.exception("Delivery to chat %s failed", chat_id)

    _last_run_date["daily"] = key


def build_scheduler(bot: Bot, state: AppState) -> AsyncIOScheduler:
    """Create and configure (but do not start) the AsyncIOScheduler."""
    tz = ZoneInfo(state.settings.timezone)
    scheduler = AsyncIOScheduler(timezone=tz)

    trigger = CronTrigger(
        hour=state.settings.daily_review_hour,
        minute=0,
        timezone=tz,
    )
    scheduler.add_job(
        deliver_daily_reviews,
        trigger=trigger,
        args=(bot, state),
        id="daily_reviews",
        name="Daily APPSC revision delivery",
        misfire_grace_time=3600,  # tolerate up to 1h late (sleeps/restarts)
        coalesce=True,            # collapse missed runs into one
        max_instances=1,
        replace_existing=True,
    )
    logger.info(
        "Scheduled daily reviews at %02d:00 %s",
        state.settings.daily_review_hour,
        state.settings.timezone,
    )
    return scheduler
