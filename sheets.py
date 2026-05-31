"""Google Sheets repository — the bot's single source of truth.

Wraps :mod:`gspread` with:

* Service-account (JWT) authentication.
* Header-driven reads (never positional) so columns can be reordered safely.
* One-time schema migration that appends the SM-2 columns (M–S) and seeds
  defaults for legacy rows.
* Retry-with-exponential-backoff around every network call to survive
  Google API 429/5xx blips.
* Per-job-run caching plus batched write-back.

The public surface is :class:`SheetsRepository` and the :class:`Card`
dataclass.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, TypeVar

import gspread
from google.oauth2.service_account import Credentials

from config import Settings
from srs import (
    DEFAULT_EASE_FACTOR,
    MASTERY_INTERVAL_DAYS,
    SrsState,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

DATE_FMT = "%Y-%m-%d"

# --- Header names (must match the sheet exactly) -------------------------- #
H_SESSION = "Session No"
H_DATE = "Date"
H_NODE = "Syllabus Node"
H_QUESTION = "Question Text"
H_FACTS = "Key Facts & Gist"
H_KEYWORDS = "Model Answer Keywords / Dimensions"
H_PROBABILITY = "Probability Rating"
H_DAY1 = "Spaced Repetition - Day 1"
H_DAY3 = "Spaced Repetition - Day 3"
H_DAY7 = "Spaced Repetition - Day 7"
H_DAY21 = "Spaced Repetition - Day 21"
H_RECALL = "Recall Status"

# New SM-2 columns appended on first run (order matters — appended L->R).
H_EASE = "Ease Factor"
H_INTERVAL = "Interval Days"
H_REPS = "Repetitions"
H_NEXT_REVIEW = "Next Review Date"
H_LAST_REVIEWED = "Last Reviewed"
H_TOTAL_REVIEWS = "Total Reviews"
H_CORRECT_COUNT = "Correct Count"

NEW_HEADERS: list[str] = [
    H_EASE,
    H_INTERVAL,
    H_REPS,
    H_NEXT_REVIEW,
    H_LAST_REVIEWED,
    H_TOTAL_REVIEWS,
    H_CORRECT_COUNT,
]

RECALL_OK = "✅"
RECALL_BAD = "❌"


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #
def parse_date(value: str | None) -> date | None:
    """Parse a ``YYYY-MM-DD`` string into a :class:`date` (or ``None``)."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in (DATE_FMT, "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    logger.warning("Unparseable date value: %r", value)
    return None


def format_date(value: date | None) -> str:
    return value.strftime(DATE_FMT) if value else ""


def _to_float(value: Any, default: float) -> float:
    try:
        text = str(value).strip()
        return float(text) if text else default
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int) -> int:
    try:
        text = str(value).strip()
        return int(float(text)) if text else default
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Card model
# --------------------------------------------------------------------------- #
@dataclass
class Card:
    """A single revision question + its SM-2 scheduling state.

    ``row_number`` is the 1-based row in the worksheet (header is row 1),
    used for targeted writes and stale-message re-fetches.
    """

    row_number: int
    session_no: str
    date: str
    node: str
    question: str
    facts: str
    keywords: str
    probability: str
    recall_status: str

    ease_factor: float = DEFAULT_EASE_FACTOR
    interval_days: int = 0
    repetitions: int = 0
    next_review_date: date | None = None
    last_reviewed: date | None = None
    total_reviews: int = 0
    correct_count: int = 0

    @property
    def srs_state(self) -> SrsState:
        return SrsState(
            ease_factor=self.ease_factor,
            interval_days=self.interval_days,
            repetitions=self.repetitions,
            next_review_date=self.next_review_date,
        )

    @property
    def is_mastered(self) -> bool:
        return self.interval_days >= MASTERY_INTERVAL_DAYS

    @property
    def recall_percent(self) -> float | None:
        if self.total_reviews <= 0:
            return None
        return 100.0 * self.correct_count / self.total_reviews

    def is_due(self, today: date) -> bool:
        return self.next_review_date is not None and self.next_review_date <= today


def _column_letter(index_zero_based: int) -> str:
    """Convert a 0-based column index to an A1 column letter (0->A)."""
    n = index_zero_based + 1
    letters = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


# --------------------------------------------------------------------------- #
# Repository
# --------------------------------------------------------------------------- #
class SheetsRepository:
    """Thin, resilient gspread wrapper for the revision sheet."""

    def __init__(self, settings: Settings, *, max_retries: int = 3) -> None:
        self._settings = settings
        self._max_retries = max_retries
        self._worksheet: gspread.Worksheet | None = None
        self._header_index: dict[str, int] = {}
        self._cache: list[Card] | None = None

    # --- connection ------------------------------------------------------- #
    def connect(self) -> None:
        """Authenticate and open the configured worksheet (idempotent)."""
        if self._worksheet is not None:
            return
        raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        if raw:
            info = json.loads(raw)
            creds = Credentials.from_service_account_info(info, scopes=SCOPES)
            logger.info("Loaded service account from GOOGLE_SERVICE_ACCOUNT_JSON env var")
        else:
            creds = Credentials.from_service_account_file(
                str(self._settings.service_account_path), scopes=SCOPES
            )
            logger.info("Loaded service account from file")
        client = gspread.authorize(creds)
        spreadsheet = self._with_retry(
            "open spreadsheet", lambda: client.open_by_key(self._settings.sheet_id)
        )
        self._worksheet = self._with_retry(
            "open worksheet",
            lambda: spreadsheet.worksheet(self._settings.worksheet_name),
        )
        logger.info(
            "Connected to sheet %s / worksheet %s",
            self._settings.sheet_id,
            self._settings.worksheet_name,
        )

    @property
    def worksheet(self) -> gspread.Worksheet:
        if self._worksheet is None:
            self.connect()
        assert self._worksheet is not None
        return self._worksheet

    # --- retry helper ----------------------------------------------------- #
    def _with_retry(self, what: str, fn: Callable[[], T]) -> T:
        """Run ``fn`` with exponential backoff on transient API errors."""
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                return fn()
            except gspread.exceptions.APIError as exc:  # 429 / 5xx
                status = getattr(getattr(exc, "response", None), "status_code", None)
                last_exc = exc
                if status is not None and status not in (429, 500, 502, 503, 504):
                    raise
                backoff = (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                logger.warning(
                    "Sheets API error on '%s' (status=%s, attempt %d/%d); "
                    "retrying in %.1fs",
                    what,
                    status,
                    attempt,
                    self._max_retries,
                    backoff,
                )
                time.sleep(backoff)
            except (TimeoutError, ConnectionError, OSError) as exc:
                last_exc = exc
                backoff = (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                logger.warning(
                    "Network error on '%s' (attempt %d/%d); retrying in %.1fs: %s",
                    what,
                    attempt,
                    self._max_retries,
                    backoff,
                    exc,
                )
                time.sleep(backoff)
        assert last_exc is not None
        logger.error("'%s' failed after %d attempts", what, self._max_retries)
        raise last_exc

    # --- schema / migration ---------------------------------------------- #
    def migrate_schema(self, *, today: date | None = None) -> int:
        """Ensure SM-2 columns exist and seed defaults for legacy rows.

        Returns the number of rows that were initialised.
        """
        today = today or date.today()
        ws = self.worksheet

        header: list[str] = self._with_retry("read header", lambda: ws.row_values(1))
        header = [h.strip() for h in header]

        missing = [h for h in NEW_HEADERS if h not in header]
        if missing:
            start_col = len(header) + 1
            cells = [
                gspread.Cell(1, start_col + i, value=h) for i, h in enumerate(missing)
            ]
            self._with_retry("append headers", lambda: ws.update_cells(cells))
            header.extend(missing)
            logger.info("Added missing columns: %s", missing)

        self._header_index = {h: i for i, h in enumerate(header)}

        records = self._with_retry(
            "read all records", lambda: ws.get_all_records(expected_headers=header)
        )

        updates: list[gspread.Cell] = []
        seeded = 0
        for offset, rec in enumerate(records):
            row_number = offset + 2  # +1 header, +1 to 1-based
            needs_seed = not str(rec.get(H_NEXT_REVIEW, "")).strip()
            if not needs_seed:
                continue
            seeded += 1
            defaults = {
                H_EASE: DEFAULT_EASE_FACTOR,
                H_INTERVAL: 0,
                H_REPS: 0,
                H_NEXT_REVIEW: format_date(today),
                H_LAST_REVIEWED: "",
                H_TOTAL_REVIEWS: 0,
                H_CORRECT_COUNT: 0,
            }
            for hdr, val in defaults.items():
                col = self._header_index[hdr] + 1
                updates.append(gspread.Cell(row_number, col, value=val))

        if updates:
            self._with_retry("seed legacy rows", lambda: ws.update_cells(updates))
            logger.info("Seeded SM-2 defaults for %d legacy row(s)", seeded)

        self._cache = None
        return seeded

    # --- reads ------------------------------------------------------------ #
    def load_cards(self, *, use_cache: bool = True) -> list[Card]:
        """Read every card. Cached for the duration of a job run."""
        if use_cache and self._cache is not None:
            return self._cache
        ws = self.worksheet
        if not self._header_index:
            header = [h.strip() for h in self._with_retry("read header", lambda: ws.row_values(1))]
            self._header_index = {h: i for i, h in enumerate(header)}
        header = list(self._header_index.keys())
        records = self._with_retry(
            "read all records", lambda: ws.get_all_records(expected_headers=header)
        )
        cards = [self._record_to_card(rec, offset + 2) for offset, rec in enumerate(records)]
        self._cache = cards
        return cards

    def invalidate_cache(self) -> None:
        self._cache = None

    def fetch_card(self, row_number: int) -> Card | None:
        """Re-read a single row fresh from the sheet (bypasses cache).

        Used when a button is tapped on a possibly-stale message.
        """
        ws = self.worksheet
        if not self._header_index:
            self.load_cards()
        header = list(self._header_index.keys())
        values = self._with_retry(
            f"read row {row_number}", lambda: ws.row_values(row_number)
        )
        if not values:
            return None
        # Pad values to header length.
        values += [""] * (len(header) - len(values))
        rec = {h: values[self._header_index[h]] for h in header}
        return self._record_to_card(rec, row_number)

    def _record_to_card(self, rec: dict[str, Any], row_number: int) -> Card:
        return Card(
            row_number=row_number,
            session_no=str(rec.get(H_SESSION, "")).strip(),
            date=str(rec.get(H_DATE, "")).strip(),
            node=str(rec.get(H_NODE, "")).strip(),
            question=str(rec.get(H_QUESTION, "")).strip(),
            facts=str(rec.get(H_FACTS, "")),
            keywords=str(rec.get(H_KEYWORDS, "")),
            probability=str(rec.get(H_PROBABILITY, "")).strip(),
            recall_status=str(rec.get(H_RECALL, "")).strip(),
            ease_factor=_to_float(rec.get(H_EASE), DEFAULT_EASE_FACTOR),
            interval_days=_to_int(rec.get(H_INTERVAL), 0),
            repetitions=_to_int(rec.get(H_REPS), 0),
            next_review_date=parse_date(rec.get(H_NEXT_REVIEW)),
            last_reviewed=parse_date(rec.get(H_LAST_REVIEWED)),
            total_reviews=_to_int(rec.get(H_TOTAL_REVIEWS), 0),
            correct_count=_to_int(rec.get(H_CORRECT_COUNT), 0),
        )

    # --- writes ----------------------------------------------------------- #
    def write_review(
        self,
        row_number: int,
        state: SrsState,
        *,
        is_correct: bool,
        total_reviews: int,
        correct_count: int,
        reviewed_on: date,
    ) -> None:
        """Persist a graded review back to a single row (batched cells)."""
        ws = self.worksheet
        if not self._header_index:
            self.load_cards()
        idx = self._header_index
        values: dict[str, Any] = {
            H_EASE: round(state.ease_factor, 4),
            H_INTERVAL: state.interval_days,
            H_REPS: state.repetitions,
            H_NEXT_REVIEW: format_date(state.next_review_date),
            H_LAST_REVIEWED: format_date(reviewed_on),
            H_TOTAL_REVIEWS: total_reviews,
            H_CORRECT_COUNT: correct_count,
            H_RECALL: RECALL_OK if is_correct else RECALL_BAD,
        }
        cells = [
            gspread.Cell(row_number, idx[hdr] + 1, value=val)
            for hdr, val in values.items()
            if hdr in idx
        ]
        self._with_retry("write review", lambda: ws.update_cells(cells))
        self.invalidate_cache()
        logger.info(
            "Review written row=%d interval=%d reps=%d next=%s correct=%s",
            row_number,
            state.interval_days,
            state.repetitions,
            format_date(state.next_review_date),
            is_correct,
        )

    def append_card(self, values_by_header: dict[str, Any], *, today: date) -> int:
        """Append a brand-new question row and return its row number."""
        ws = self.worksheet
        if not self._header_index:
            self.load_cards()
        header = list(self._header_index.keys())

        seed = {
            H_RECALL: "",
            H_EASE: DEFAULT_EASE_FACTOR,
            H_INTERVAL: 0,
            H_REPS: 0,
            H_NEXT_REVIEW: format_date(today),
            H_LAST_REVIEWED: "",
            H_TOTAL_REVIEWS: 0,
            H_CORRECT_COUNT: 0,
        }
        merged = {**seed, **values_by_header}
        row = [merged.get(h, "") for h in header]
        self._with_retry(
            "append card",
            lambda: ws.append_row(row, value_input_option="USER_ENTERED"),
        )
        self.invalidate_cache()
        # Row number = current row count after append.
        count = self._with_retry("row count", lambda: len(ws.col_values(1)))
        logger.info("Appended new card at row %d", count)
        return count
