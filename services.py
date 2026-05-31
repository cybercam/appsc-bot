"""Shared application services: app state, chat registry, card delivery.

This wires the :class:`~sheets.SheetsRepository` and configuration into a
single object stored on the bot's ``application.bot_data`` so every command
handler, callback and scheduled job can reach them.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError

import messages
from config import Settings
from sheets import Card, SheetsRepository

logger = logging.getLogger(__name__)

# Telegram allows ~30 msgs/sec but we deliberately throttle to 1/sec so a
# big due-queue feels like a calm drip, not a flood.
SEND_INTERVAL_SECONDS = 1.0

BOT_DATA_KEY = "state"


@dataclass
class AppState:
    """Container for everything the handlers and jobs need at runtime."""

    settings: Settings
    repo: SheetsRepository
    registered_chats: set[int] = field(default_factory=set)

    # --- chat registry persistence --------------------------------------- #
    def load_chats(self) -> None:
        path = self.settings.chat_ids_path
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self.registered_chats = {int(x) for x in data}
                logger.info("Loaded %d registered chat(s)", len(self.registered_chats))
            except (json.JSONDecodeError, ValueError, OSError) as exc:
                logger.warning("Could not read chat registry: %s", exc)
                self.registered_chats = set()

    def save_chats(self) -> None:
        path = self.settings.chat_ids_path
        try:
            path.write_text(
                json.dumps(sorted(self.registered_chats)), encoding="utf-8"
            )
        except OSError as exc:
            logger.error("Could not persist chat registry: %s", exc)

    def register_chat(self, chat_id: int) -> bool:
        """Add ``chat_id``; return True if newly added."""
        if chat_id in self.registered_chats:
            return False
        self.registered_chats.add(chat_id)
        self.save_chats()
        return True

    def is_authorized(self, chat_id: int) -> bool:
        return self.settings.is_authorized(chat_id, self.registered_chats)

    # --- recipients for scheduled delivery ------------------------------- #
    def delivery_targets(self) -> set[int]:
        if self.settings.authorized_ids:
            # Only authorised chats that have also started the bot.
            return self.settings.authorized_ids & self.registered_chats or set(
                self.settings.authorized_ids
            )
        return set(self.registered_chats)


# --------------------------------------------------------------------------- #
# Delivery helpers
# --------------------------------------------------------------------------- #
async def send_card(bot: Bot, chat_id: int, card: Card) -> None:
    """Send a single card as an active-recall prompt with a Show Facts button."""
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=messages.render_question(card),
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=messages.show_facts_keyboard(card),
        )
    except RetryAfter as exc:
        logger.warning("Rate limited; sleeping %.1fs", exc.retry_after)
        await asyncio.sleep(float(exc.retry_after) + 0.5)
        await send_card(bot, chat_id, card)
    except TelegramError as exc:
        logger.error("Failed to send card row=%d to %d: %s", card.row_number, chat_id, exc)


async def send_due_cards(
    bot: Bot, chat_id: int, cards: list[Card], *, throttle: float = SEND_INTERVAL_SECONDS
) -> int:
    """Send each due card one at a time, throttled to respect rate limits."""
    sent = 0
    for card in cards:
        await send_card(bot, chat_id, card)
        sent += 1
        if throttle > 0 and card is not cards[-1]:
            await asyncio.sleep(throttle)
    return sent


def today_in_tz(tz_name: str) -> date:
    """Return the current calendar date in the configured timezone."""
    try:
        return datetime.now(ZoneInfo(tz_name)).date()
    except Exception:  # noqa: BLE001 - fall back to naive local date
        return date.today()


def due_cards(cards: list[Card], today: date) -> list[Card]:
    """Filter + sort cards due on/before ``today`` (oldest first)."""
    due = [c for c in cards if c.is_due(today)]
    due.sort(key=lambda c: (c.next_review_date or today, c.session_no))
    return due
