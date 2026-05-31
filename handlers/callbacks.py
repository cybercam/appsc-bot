"""Inline-button callbacks: reveal facts and grade recall (SM-2)."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

import messages
from services import BOT_DATA_KEY, AppState, today_in_tz
from srs import apply_grade, quality_from_grade

logger = logging.getLogger(__name__)


def _state(context: ContextTypes.DEFAULT_TYPE) -> AppState:
    return context.application.bot_data[BOT_DATA_KEY]


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch every inline-button tap to the right handler."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    state = _state(context)
    chat_id = query.message.chat_id if query.message else None

    if chat_id is not None and not state.is_authorized(chat_id):
        await query.answer("Not authorized.", show_alert=True)
        return

    action, params = messages.parse_callback(query.data)

    if action == "show":
        await _handle_show(query, state, params)
    elif action == "grade":
        await _handle_grade(query, state, params)
    else:
        await query.answer()


async def _handle_show(query, state: AppState, params: dict[str, str]) -> None:
    """Reveal Key Facts + Keywords, then show the grading row."""
    row = int(params["row"])
    # Always re-fetch fresh state so stale messages still work post-restart.
    card = state.repo.fetch_card(row)
    if card is None:
        await query.answer("This card no longer exists.", show_alert=True)
        return
    await query.answer()
    try:
        await query.edit_message_text(
            text=messages.render_question_with_facts(card),
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=messages.grading_keyboard(card),
        )
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            logger.warning("edit_message_text failed (show) row=%d: %s", row, exc)


async def _handle_grade(query, state: AppState, params: dict[str, str]) -> None:
    """Apply an SM-2 grade, persist it, and confirm to the user."""
    row = int(params["row"])
    grade = params["grade"]

    card = state.repo.fetch_card(row)
    if card is None:
        await query.answer("This card no longer exists.", show_alert=True)
        return

    today = today_in_tz(state.settings.timezone)
    quality = quality_from_grade(grade)
    result = apply_grade(card.srs_state, quality, today=today)

    new_total = card.total_reviews + 1
    new_correct = card.correct_count + (1 if result.is_correct else 0)

    try:
        state.repo.write_review(
            row,
            result.state,
            is_correct=result.is_correct,
            total_reviews=new_total,
            correct_count=new_correct,
            reviewed_on=today,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to write review for row=%d", row)
        await query.answer("Could not save — try again.", show_alert=True)
        return

    await query.answer("Saved ✅")
    logger.info(
        "REVIEW session=%s row=%d grade=%s q=%d interval=%d reps=%d",
        card.session_no,
        row,
        grade,
        quality,
        result.state.interval_days,
        result.state.repetitions,
    )

    confirmation = messages.render_review_result(
        card,
        grade=grade,
        next_review=_fmt(result.state.next_review_date),
        interval=result.state.interval_days,
    )
    revealed = messages.render_question_with_facts(card)
    try:
        await query.edit_message_text(
            text=f"{revealed}\n\n{confirmation}",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=None,
        )
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            logger.warning("edit_message_text failed (grade) row=%d: %s", row, exc)


def _fmt(d) -> str:
    return d.strftime("%Y-%m-%d") if d else ""


async def on_plain_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Nudge users who type instead of tapping buttons."""
    state = _state(context)
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None or not state.is_authorized(chat_id):
        return
    await update.effective_message.reply_text(
        messages.USE_BUTTONS_HINT, parse_mode=ParseMode.MARKDOWN_V2
    )
