"""Entrypoint: configure logging, wire handlers, run bot + scheduler.

Run locally::

    python main.py

The process is a long-running worker (no webhook); it uses long polling so
it works on any free-tier worker dyno without an inbound URL.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import get_settings
from handlers import callbacks, commands
from scheduler import build_scheduler
from services import BOT_DATA_KEY, AppState
from sheets import SheetsRepository

logger = logging.getLogger("appsc_bot")


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
def configure_logging(level: str, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # Quieten noisy third-party loggers.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.INFO)


# --------------------------------------------------------------------------- #
# Error handler
# --------------------------------------------------------------------------- #
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error while processing update: %s", context.error)


# --------------------------------------------------------------------------- #
# Handler registration
# --------------------------------------------------------------------------- #
def register_handlers(app: Application) -> None:
    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add", commands.add_start)],
        states={
            commands.ADD_SESSION: [MessageHandler(filters.TEXT & ~filters.COMMAND, commands.add_session)],
            commands.ADD_NODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, commands.add_node)],
            commands.ADD_QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, commands.add_question)],
            commands.ADD_FACTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, commands.add_facts)],
            commands.ADD_KEYWORDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, commands.add_keywords)],
            commands.ADD_PROBABILITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, commands.add_probability)],
        },
        fallbacks=[CommandHandler("cancel", commands.add_cancel)],
        name="add_question",
        persistent=False,
    )

    app.add_handler(CommandHandler("start", commands.start))
    app.add_handler(CommandHandler("help", commands.help_command))
    app.add_handler(CommandHandler("today", commands.today))
    app.add_handler(CommandHandler("review", commands.review))
    app.add_handler(CommandHandler("weak", commands.weak))
    app.add_handler(CommandHandler("stats", commands.stats))
    app.add_handler(CommandHandler("search", commands.search))
    app.add_handler(add_conv)

    app.add_handler(CallbackQueryHandler(callbacks.on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, callbacks.on_plain_text))

    app.add_error_handler(on_error)


async def _post_init(app: Application) -> None:
    """Runs after the Application starts: migrate sheet, start scheduler."""
    state: AppState = app.bot_data[BOT_DATA_KEY]

    # Connect + migrate schema (adds M–S, seeds legacy rows).
    state.repo.connect()
    seeded = state.repo.migrate_schema()
    logger.info("Schema migration complete (%d legacy row(s) seeded).", seeded)

    # Register the command menu shown in Telegram clients.
    await app.bot.set_my_commands(
        [
            BotCommand("today", "Cards due today"),
            BotCommand("review", "Drill all ❌ cards"),
            BotCommand("weak", "Weakest topics"),
            BotCommand("stats", "Your dashboard"),
            BotCommand("search", "Search questions"),
            BotCommand("add", "Add a question"),
            BotCommand("help", "Show commands"),
        ]
    )

    # Start the daily scheduler bound to this bot + state.
    scheduler = build_scheduler(app.bot, state)
    scheduler.start()
    app.bot_data["scheduler"] = scheduler
    logger.info("Bot is up. Polling for updates…")


async def _post_shutdown(app: Application) -> None:
    scheduler = app.bot_data.get("scheduler")
    if scheduler is not None:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")


def build_application() -> Application:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_path)

    repo = SheetsRepository(settings)
    state = AppState(settings=settings, repo=repo)
    state.load_chats()

    app = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.bot_data[BOT_DATA_KEY] = state
    register_handlers(app)
    return app


def main() -> None:
    app = build_application()
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
