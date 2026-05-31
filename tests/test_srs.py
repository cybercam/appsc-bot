"""Unit tests for the SM-2 algorithm in :mod:`srs`."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from srs import (
    DEFAULT_EASE_FACTOR,
    MIN_EASE_FACTOR,
    SrsState,
    apply_grade,
    quality_from_grade,
)

TODAY = date(2024, 1, 1)


def fresh() -> SrsState:
    return SrsState()


# --------------------------------------------------------------------------- #
# Grade label mapping
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("label", "expected"),
    [("again", 2), ("Hard", 3), ("GOOD", 4), (" easy ", 5)],
)
def test_quality_from_grade(label: str, expected: int) -> None:
    assert quality_from_grade(label) == expected


def test_quality_from_grade_invalid() -> None:
    with pytest.raises(ValueError):
        quality_from_grade("nope")


# --------------------------------------------------------------------------- #
# First review of a fresh card
# --------------------------------------------------------------------------- #
def test_first_good_review_interval_is_one() -> None:
    result = apply_grade(fresh(), quality=4, today=TODAY)
    assert result.state.interval_days == 1
    assert result.state.repetitions == 1
    assert result.is_correct is True
    assert result.is_mastered is False
    assert result.state.next_review_date == TODAY + timedelta(days=1)


def test_second_good_review_interval_is_three() -> None:
    s = apply_grade(fresh(), quality=4, today=TODAY).state
    result = apply_grade(s, quality=4, today=TODAY)
    assert result.state.interval_days == 3
    assert result.state.repetitions == 2


def test_third_good_review_uses_ease_factor() -> None:
    s = fresh()
    for _ in range(2):
        s = apply_grade(s, quality=4, today=TODAY).state
    # interval was 3, repetitions == 2 -> interval = round(3 * ef)
    result = apply_grade(s, quality=4, today=TODAY)
    expected = round(3 * s.ease_factor)
    assert result.state.interval_days == expected
    assert result.state.repetitions == 3


# --------------------------------------------------------------------------- #
# "Again" (q=2) lapse behaviour
# --------------------------------------------------------------------------- #
def test_again_resets_repetitions_and_interval() -> None:
    s = fresh()
    for _ in range(3):
        s = apply_grade(s, quality=4, today=TODAY).state
    assert s.repetitions == 3

    result = apply_grade(s, quality=2, today=TODAY)
    assert result.state.repetitions == 0
    assert result.state.interval_days == 1
    assert result.is_correct is False
    assert result.state.next_review_date == TODAY + timedelta(days=1)


def test_again_lowers_ease_factor() -> None:
    result = apply_grade(fresh(), quality=2, today=TODAY)
    assert result.state.ease_factor < DEFAULT_EASE_FACTOR


# --------------------------------------------------------------------------- #
# Ease factor floor
# --------------------------------------------------------------------------- #
def test_ease_factor_never_below_minimum() -> None:
    s = fresh()
    for _ in range(20):
        s = apply_grade(s, quality=2, today=TODAY).state
    assert s.ease_factor >= MIN_EASE_FACTOR


def test_easy_raises_ease_factor() -> None:
    result = apply_grade(fresh(), quality=5, today=TODAY)
    assert result.state.ease_factor > DEFAULT_EASE_FACTOR


def test_hard_lowers_ease_factor_but_still_correct() -> None:
    result = apply_grade(fresh(), quality=3, today=TODAY)
    assert result.is_correct is True
    assert result.state.ease_factor < DEFAULT_EASE_FACTOR


# --------------------------------------------------------------------------- #
# Mastery detection across a long "Good"/"Easy" streak
# --------------------------------------------------------------------------- #
def test_card_eventually_masters() -> None:
    s = fresh()
    mastered = False
    for _ in range(8):
        result = apply_grade(s, quality=5, today=TODAY)
        s = result.state
        mastered = mastered or result.is_mastered
    assert mastered is True
    assert s.interval_days >= 21


# --------------------------------------------------------------------------- #
# All four grades on a fresh card
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("label", "q", "correct"),
    [("again", 2, False), ("hard", 3, True), ("good", 4, True), ("easy", 5, True)],
)
def test_all_grades_fresh_card(label: str, q: int, correct: bool) -> None:
    result = apply_grade(fresh(), quality=quality_from_grade(label), today=TODAY)
    assert result.quality == q
    assert result.is_correct is correct
    assert result.state.interval_days >= 1


def test_invalid_quality_raises() -> None:
    with pytest.raises(ValueError):
        apply_grade(fresh(), quality=9, today=TODAY)
