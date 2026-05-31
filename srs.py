"""SM-2 (Anki-style) spaced-repetition algorithm.

This module contains *pure* functions only — no I/O, no globals, no side
effects — so the scheduling logic is trivially unit-testable and reusable.

The four user-facing grades map to SM-2 quality scores as follows::

    "Again" -> q = 2   (failed recall)
    "Hard"  -> q = 3
    "Good"  -> q = 4
    "Easy"  -> q = 5

A quality score ``q < 3`` is treated as a lapse: repetitions reset to 0 and
the card is shown again the next day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

# Default SM-2 parameters used when a card has never been reviewed.
DEFAULT_EASE_FACTOR: float = 2.5
MIN_EASE_FACTOR: float = 1.3

# A card is considered "mastered" once its interval reaches this many days.
MASTERY_INTERVAL_DAYS: int = 21

# Mapping from inline-button grade labels to SM-2 quality scores.
GRADE_TO_QUALITY: dict[str, int] = {
    "again": 2,
    "hard": 3,
    "good": 4,
    "easy": 5,
}


@dataclass(frozen=True)
class SrsState:
    """The mutable spaced-repetition state stored for a single card.

    Mirrors sheet columns M (ease_factor), N (interval_days),
    O (repetitions) and P (next_review_date).
    """

    ease_factor: float = DEFAULT_EASE_FACTOR
    interval_days: int = 0
    repetitions: int = 0
    next_review_date: date | None = None


@dataclass(frozen=True)
class SrsResult:
    """Outcome of applying a grade: the new state plus derived flags."""

    state: SrsState
    is_correct: bool
    is_mastered: bool
    quality: int


def quality_from_grade(grade: str) -> int:
    """Translate a button grade label (e.g. ``"good"``) to a quality score.

    Args:
        grade: One of ``again``/``hard``/``good``/``easy`` (case-insensitive).

    Returns:
        The integer SM-2 quality score.

    Raises:
        ValueError: If ``grade`` is not a recognised label.
    """
    key = grade.strip().lower()
    if key not in GRADE_TO_QUALITY:
        raise ValueError(f"Unknown grade label: {grade!r}")
    return GRADE_TO_QUALITY[key]


def _next_ease_factor(ease_factor: float, quality: int) -> float:
    """Compute the updated ease factor, clamped to the SM-2 minimum."""
    delta = 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)
    return max(MIN_EASE_FACTOR, ease_factor + delta)


def apply_grade(
    state: SrsState,
    quality: int,
    *,
    today: date | None = None,
) -> SrsResult:
    """Apply an SM-2 review to ``state`` and return the new state.

    Args:
        state: The card's current SRS state.
        quality: SM-2 quality score in the range 2..5.
        today: The reference "now" date. Defaults to ``date.today()``;
            injectable for deterministic testing.

    Returns:
        An :class:`SrsResult` carrying the updated :class:`SrsState` and a
        few convenience flags (``is_correct``, ``is_mastered``).
    """
    if today is None:
        today = date.today()
    if not 0 <= quality <= 5:
        raise ValueError(f"quality must be in 0..5, got {quality}")

    is_correct = quality >= 3

    if not is_correct:
        # Lapse: reset progress and re-show tomorrow.
        repetitions = 0
        interval = 1
    else:
        if state.repetitions == 0:
            interval = 1
        elif state.repetitions == 1:
            interval = 3
        else:
            interval = round(state.interval_days * state.ease_factor)
        repetitions = state.repetitions + 1

    interval = max(1, interval)
    ease_factor = _next_ease_factor(state.ease_factor, quality)
    next_review = today + timedelta(days=interval)

    new_state = SrsState(
        ease_factor=ease_factor,
        interval_days=interval,
        repetitions=repetitions,
        next_review_date=next_review,
    )
    return SrsResult(
        state=new_state,
        is_correct=is_correct,
        is_mastered=interval >= MASTERY_INTERVAL_DAYS,
        quality=quality,
    )
