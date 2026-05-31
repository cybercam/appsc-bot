"""Slash-command handlers: /start /today /review /weak /stats /search /add /help."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
)

import messages
from services import (
    BOT_DATA_KEY,
    AppState,
    due_cards,
    send_due_cards,
    today_in_tz,
)
from sheets import (
    Card,
    H_DATE,
    H_FACTS,
    H_KEYWORDS,
    H_NODE,
    H_PROBABILITY,
    H_QUESTION,
    H_SESSION,
    RECALL_BAD,
)

logger = logging.getLogger(__name__)

# Conversation states for /add
(
    ADD_SESSION,
    ADD_NODE,
    ADD_QUESTION,
    ADD_FACTS,
    ADD_KEYWORDS,
    ADD_PROBABILITY,
) = range(6)


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def get_state(context: ContextTypes.DEFAULT_TYPE) -> AppState:
    return context.application.bot_data[BOT_DATA_KEY]


def _today(state: AppState) -> date:
    return today_in_tz(state.settings.timezone)


async def _guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> AppState | None:
    """Return AppState if the chat is authorized, else reply and return None."""
    state = get_state(context)
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return None
    if not state.is_authorized(chat_id):
        await update.effective_message.reply_text(
            messages.NOT_AUTHORIZED, parse_mode=ParseMode.MARKDOWN_V2
        )
        return None
    return state


async def _reply(update: Update, text: str) -> None:
    await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


# --------------------------------------------------------------------------- #
# /start, /help
# --------------------------------------------------------------------------- #
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = get_state(context)
    chat_id = update.effective_chat.id
    state.register_chat(chat_id)
    # If an allowlist is configured and this chat isn't on it, tell them.
    if state.settings.authorized_ids and chat_id not in state.settings.authorized_ids:
        await _reply(update, messages.NOT_AUTHORIZED)
        logger.info("Unauthorized /start from chat_id=%s", chat_id)
        return
    logger.info("Registered chat_id=%s", chat_id)
    await _reply(update, messages.WELCOME)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _guard(update, context) is None:
        return
    await _reply(update, messages.HELP)


# --------------------------------------------------------------------------- #
# /today
# --------------------------------------------------------------------------- #
async def today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = await _guard(update, context)
    if state is None:
        return
    cards = state.repo.load_cards(use_cache=False)
    due = due_cards(cards, _today(state))
    await _reply(update, messages.render_due_header(len(due)))
    if due:
        await send_due_cards(context.bot, update.effective_chat.id, due)


# --------------------------------------------------------------------------- #
# /review — all cards currently marked ❌
# --------------------------------------------------------------------------- #
async def review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = await _guard(update, context)
    if state is None:
        return
    cards = state.repo.load_cards(use_cache=False)
    failing = [c for c in cards if c.recall_status == RECALL_BAD]
    failing.sort(key=lambda c: c.session_no)
    if not failing:
        await _reply(update, "✅ No cards marked ❌ — nothing to drill\\. Great job\\!")
        return
    await _reply(update, f"🩹 *{len(failing)}* card\\(s\\) need drilling\\. Here they come…")
    await send_due_cards(context.bot, update.effective_chat.id, failing)


# --------------------------------------------------------------------------- #
# /weak — topics ranked by lowest recall %
# --------------------------------------------------------------------------- #
async def weak(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = await _guard(update, context)
    if state is None:
        return
    cards = state.repo.load_cards(use_cache=False)
    agg: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # node -> [correct, total]
    for c in cards:
        if c.total_reviews > 0:
            agg[c.node][0] += c.correct_count
            agg[c.node][1] += c.total_reviews
    rows = [
        (node, 100.0 * correct / total, correct, total)
        for node, (correct, total) in agg.items()
        if total > 0
    ]
    rows.sort(key=lambda r: r[1])  # lowest recall first
    await _reply(update, messages.render_weak(rows))


# --------------------------------------------------------------------------- #
# /stats — dashboard
# --------------------------------------------------------------------------- #
def _compute_streak(review_dates: set[date], today_date: date) -> int:
    """Consecutive days (ending today or yesterday) with >=1 review."""
    if not review_dates:
        return 0
    anchor = today_date if today_date in review_dates else today_date - timedelta(days=1)
    if anchor not in review_dates:
        return 0
    streak = 0
    cursor = anchor
    while cursor in review_dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = await _guard(update, context)
    if state is None:
        return
    today_date = _today(state)
    cards = state.repo.load_cards(use_cache=False)

    total = len(cards)
    total_reviews = sum(c.total_reviews for c in cards)
    total_correct = sum(c.correct_count for c in cards)
    overall_pct = (100.0 * total_correct / total_reviews) if total_reviews else None
    mastered = sum(1 for c in cards if c.is_mastered)
    due = len(due_cards(cards, today_date))

    review_dates = {c.last_reviewed for c in cards if c.last_reviewed}
    streak = _compute_streak(review_dates, today_date)

    node_agg: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])  # count, correct, reviews
    for c in cards:
        node_agg[c.node][0] += 1
        node_agg[c.node][1] += c.correct_count
        node_agg[c.node][2] += c.total_reviews
    per_node = []
    for node, (count, correct, reviews) in sorted(node_agg.items()):
        pct = (100.0 * correct / reviews) if reviews else None
        per_node.append((node, count, pct))

    await _reply(
        update,
        messages.render_stats(
            total=total,
            total_reviews=total_reviews,
            overall_pct=overall_pct,
            streak=streak,
            mastered=mastered,
            due_today=due,
            per_node=per_node,
        ),
    )


# --------------------------------------------------------------------------- #
# /search <keyword>
# --------------------------------------------------------------------------- #
async def search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = await _guard(update, context)
    if state is None:
        return
    keyword = " ".join(context.args).strip() if context.args else ""
    if not keyword:
        await _reply(update, "Usage: `/search <keyword>`")
        return
    needle = keyword.lower()
    cards = state.repo.load_cards(use_cache=False)
    matches = [
        c
        for c in cards
        if needle in c.question.lower() or needle in c.node.lower()
    ]
    matches.sort(key=lambda c: c.session_no)
    await _reply(update, messages.render_search_results(keyword, matches))


# --------------------------------------------------------------------------- #
# /add — guided new-question flow (ConversationHandler)
# --------------------------------------------------------------------------- #
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    state = await _guard(update, context)
    if state is None:
        return ConversationHandler.END
    context.user_data["new_card"] = {}
    await update.effective_message.reply_text(
        "➕ Adding a new question. Send /cancel anytime.\n\n"
        "1/6 — What is the *Session No*?",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return ADD_SESSION


async def add_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["new_card"][H_SESSION] = update.effective_message.text.strip()
    await update.effective_message.reply_text("2/6 — Syllabus Node?")
    return ADD_NODE


async def add_node(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["new_card"][H_NODE] = update.effective_message.text.strip()
    await update.effective_message.reply_text("3/6 — Question Text?")
    return ADD_QUESTION


async def add_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["new_card"][H_QUESTION] = update.effective_message.text.strip()
    await update.effective_message.reply_text(
        "4/6 — Key Facts & Gist? (multi-line is fine; one bullet per line)"
    )
    return ADD_FACTS


async def add_facts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["new_card"][H_FACTS] = update.effective_message.text
    await update.effective_message.reply_text(
        "5/6 — Model Answer Keywords / Dimensions? (pipe-separated, e.g. `A | B | C`)"
    )
    return ADD_KEYWORDS


async def add_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["new_card"][H_KEYWORDS] = update.effective_message.text.strip()
    await update.effective_message.reply_text("6/6 — Probability Rating? (e.g. High/Medium/Low)")
    return ADD_PROBABILITY


async def add_probability(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    state = get_state(context)
    data = context.user_data.get("new_card", {})
    data[H_PROBABILITY] = update.effective_message.text.strip()
    today_date = _today(state)
    data[H_DATE] = today_date.strftime("%Y-%m-%d")
    try:
        row = state.repo.append_card(data, today=today_date)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to append card")
        await _reply(update, f"❌ Could not save the card: {messages.escape(str(exc))}")
        return ConversationHandler.END
    context.user_data.pop("new_card", None)
    await _reply(
        update,
        f"✅ Saved as row *{row}* and queued for review today\\. Use /today to revise\\.",
    )
    return ConversationHandler.END


async def add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("new_card", None)
    await update.effective_message.reply_text("Cancelled. Nothing was saved.")
    return ConversationHandler.END
