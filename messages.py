"""MarkdownV2 message builders and inline-keyboard factories.

Centralising every user-facing string here keeps Telegram's strict
MarkdownV2 escaping rules in one place and makes the handlers readable.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from sheets import Card

# Characters that MUST be escaped in Telegram MarkdownV2.
_MDV2_SPECIALS = r"_*[]()~`>#+-=|{}.!"


def escape(text: str | None) -> str:
    """Escape a plain string for safe use inside MarkdownV2."""
    if text is None:
        return ""
    out = []
    for ch in str(text):
        if ch in _MDV2_SPECIALS:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


# --------------------------------------------------------------------------- #
# Callback-data scheme
# --------------------------------------------------------------------------- #
# Compact strings stay well under Telegram's 64-byte callback-data limit.
#   show:<row>            -> reveal facts for card at sheet <row>
#   grade:<row>:<grade>   -> grade the card (again/hard/good/easy)
def cb_show(row: int) -> str:
    return f"show:{row}"


def cb_grade(row: int, grade: str) -> str:
    return f"grade:{row}:{grade}"


def parse_callback(data: str) -> tuple[str, dict[str, str]]:
    """Parse callback data into ``(action, params)``."""
    parts = data.split(":")
    action = parts[0]
    if action == "show":
        return action, {"row": parts[1]}
    if action == "grade":
        return action, {"row": parts[1], "grade": parts[2]}
    return action, {}


# --------------------------------------------------------------------------- #
# Keyboards
# --------------------------------------------------------------------------- #
def show_facts_keyboard(card: Card) -> InlineKeyboardMarkup:
    """The pre-recall keyboard: a single 'Show Facts' button."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔎 Show Facts", callback_data=cb_show(card.row_number))]]
    )


def grading_keyboard(card: Card) -> InlineKeyboardMarkup:
    """The SM-2 grading row shown after facts are revealed."""
    row = card.row_number
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔁 Again", callback_data=cb_grade(row, "again")),
                InlineKeyboardButton("😬 Hard", callback_data=cb_grade(row, "hard")),
                InlineKeyboardButton("🙂 Good", callback_data=cb_grade(row, "good")),
                InlineKeyboardButton("😎 Easy", callback_data=cb_grade(row, "easy")),
            ]
        ]
    )


# --------------------------------------------------------------------------- #
# Card rendering
# --------------------------------------------------------------------------- #
def render_question(card: Card) -> str:
    """The initial 'active recall' prompt (no answer revealed)."""
    node = escape(card.node or "General")
    prob = escape(card.probability or "—")
    qno = escape(card.session_no or "?")
    question = escape(card.question or "(no question text)")
    return (
        f"📚 *Revision Due* — {node}\n"
        f"🎯 *Probability:* {prob}\n\n"
        f"*Q{qno}:* {question}\n\n"
        f"_Tap below when you've recalled it\\._"
    )


def _bulletize(text: str) -> str:
    """Escape a multi-line cell, preserving line breaks."""
    lines = [ln.strip() for ln in str(text).splitlines() if ln.strip()]
    return "\n".join(escape(ln) for ln in lines)


def _keywords(text: str) -> str:
    parts = [p.strip() for p in str(text).split("|") if p.strip()]
    return escape(" • ".join(parts)) if parts else ""


def render_question_with_facts(card: Card) -> str:
    """Question + revealed Key Facts (E) and Model Answer Keywords (F)."""
    base = render_question(card)
    facts = _bulletize(card.facts)
    keywords = _keywords(card.keywords)

    body = [base, "\n*🧠 Key Facts & Gist:*"]
    body.append(facts if facts else escape("(none recorded)"))
    if keywords:
        body.append("\n*🗝️ Model Answer Keywords:*")
        body.append(keywords)
    body.append("\n_Grade your recall:_")
    return "\n".join(body)


def render_review_result(card: Card, *, grade: str, next_review: str, interval: int) -> str:
    """A short confirmation appended after grading."""
    emoji = {"again": "🔁", "hard": "😬", "good": "🙂", "easy": "😎"}.get(grade, "✅")
    return (
        f"{emoji} *Graded {escape(grade.title())}* — "
        f"next review in *{interval}* day\\(s\\) "
        f"\\({escape(next_review)}\\)\\."
    )


# --------------------------------------------------------------------------- #
# Static messages
# --------------------------------------------------------------------------- #
WELCOME = (
    "👋 *Welcome to the APPSC Group\\-1 Revision Bot\\!*\n\n"
    "I'll send you spaced\\-repetition flashcards every morning at "
    "*7:00 AM IST* and track your recall using the SM\\-2 algorithm "
    "\\(like Anki\\)\\.\n\n"
    "This chat is now registered ✅\n\n"
    "Try /today to see what's due, or /help for all commands\\."
)

HELP = (
    "*📖 Commands*\n\n"
    "/today — Cards due today\n"
    "/review — Rapid\\-fire all cards marked ❌\n"
    "/weak — Topics ranked by lowest recall %\n"
    "/stats — Your full revision dashboard\n"
    "/search `<keyword>` — Find questions by keyword\n"
    "/add — Add a new question \\(guided\\)\n"
    "/start — Register this chat\n"
    "/help — This message\n\n"
    "_Daily delivery: 7:00 AM IST\\. Grade each card with "
    "Again / Hard / Good / Easy\\._"
)

NOTHING_DUE = "🎉 *Nothing due right now\\!* Enjoy the break — I'll ping you when cards are ready\\."

USE_BUTTONS_HINT = (
    "👉 Please use the inline buttons under the card to grade your recall "
    "\\(or send /today to fetch due cards\\)\\."
)

NOT_AUTHORIZED = "🚫 This bot is private\\. Ask the owner to authorise your chat ID\\."


def render_due_header(count: int) -> str:
    if count == 0:
        return NOTHING_DUE
    return f"📬 *{count}* card\\(s\\) due\\. Sending them one at a time…"


def render_weak(rows: list[tuple[str, float, int, int]]) -> str:
    """Render the /weak ranking. rows: (node, pct, correct, total)."""
    if not rows:
        return "✨ No reviewed cards yet — nothing to rank\\. Try /today first\\."
    out = ["*🪫 Weakest Topics* \\(by recall %\\)\n"]
    for node, pct, correct, total in rows:
        out.append(
            f"• *{escape(node or 'General')}* — "
            f"{escape(f'{pct:.0f}%')} \\({correct}/{total}\\)"
        )
    return "\n".join(out)


def render_stats(
    *,
    total: int,
    total_reviews: int,
    overall_pct: float | None,
    streak: int,
    mastered: int,
    due_today: int,
    per_node: list[tuple[str, int, float | None]],
) -> str:
    """Render the /stats dashboard."""
    pct_text = f"{overall_pct:.0f}%" if overall_pct is not None else "—"
    lines = [
        "*📊 Revision Dashboard*\n",
        f"📚 Total questions: *{total}*",
        f"🔁 Reviews done: *{total_reviews}*",
        f"🎯 Overall recall: *{escape(pct_text)}*",
        f"🔥 Current streak: *{streak}* day\\(s\\)",
        f"🏆 Mastered \\(≥21d\\): *{mastered}*",
        f"📬 Due today: *{due_today}*",
        "\n*Per\\-node recall:*",
    ]
    if not per_node:
        lines.append(escape("(no reviews yet)"))
    else:
        for node, count, pct in per_node:
            pct_str = f"{pct:.0f}%" if pct is not None else "—"
            lines.append(
                f"• {escape(node or 'General')} — "
                f"{escape(pct_str)} \\({count} Q\\)"
            )
    return "\n".join(lines)


def render_search_results(keyword: str, cards: list[Card]) -> str:
    if not cards:
        return f"🔍 No questions found for *{escape(keyword)}*\\."
    out = [f"🔍 *{len(cards)}* match\\(es\\) for *{escape(keyword)}*:\n"]
    for c in cards[:20]:
        out.append(f"• *Q{escape(c.session_no)}* \\[{escape(c.node)}\\]: {escape(c.question[:90])}")
    if len(cards) > 20:
        out.append(escape(f"…and {len(cards) - 20} more."))
    return "\n".join(out)
