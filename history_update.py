"""Safe merge logic for permanent gold-rate history."""

from dataclasses import dataclass


PRICE_FIELDS = [
    "price_22k_1g_published",
    "price_22k_8g_published",
    "price_22k_1g_from_pavan",
    "difference_22k",
    "normalized_price_22k_1g",
]


@dataclass
class HistoryConflict:
    """A changed value found for an existing observation."""

    date: str
    session: str
    field: str
    stored_value: str
    observed_value: str


class HistoryConflictError(Exception):
    """Raised when observed data conflicts with permanent history."""

    def __init__(self, conflicts):
        self.conflicts = conflicts

        super().__init__(
            f"{len(conflicts)} permanent-history conflict(s) found"
        )


@dataclass
class MergeResult:
    """Result of an immutable permanent-history merge."""

    rows: list
    added_count: int
    unchanged_count: int


def history_key(row):
    """Return the unique date-and-session key."""

    return (
        row.get("date", "").strip(),
        row.get("session", "").strip(),
    )


def session_rank(session):
    """Return a stable chronological session rank."""

    order = {
        "": 0,
        "Early Morning": 1,
        "Morning": 2,
        "Forenoon": 3,
        "Noon": 4,
        "Afternoon": 5,
        "Evening": 6,
        "Night": 7,
    }

    return order.get(session, 50)


def history_sort_key(row):
    """Sort by date and chronological session."""

    date_text, session = history_key(row)

    return (
        date_text,
        session_rank(session),
        session,
    )


def normalize_value(value):
    """Normalize optional CSV text for comparison."""

    if value is None:
        return ""

    return str(value).strip().replace(",", "")


def find_price_conflicts(stored_row, observed_row):
    """Compare immutable price fields in matching observations."""

    date_text, session = history_key(stored_row)
    conflicts = []

    for field in PRICE_FIELDS:
        stored_value = normalize_value(
            stored_row.get(field)
        )

        observed_value = normalize_value(
            observed_row.get(field)
        )

        if stored_value != observed_value:
            conflicts.append(
                HistoryConflict(
                    date=date_text,
                    session=session,
                    field=field,
                    stored_value=stored_value,
                    observed_value=observed_value,
                )
            )

    return conflicts


def merge_history_rows(existing_rows, observed_rows):
    """
    Merge newly observed rows into permanent history.

    Rules:
    - New date/session keys are appended.
    - Exact duplicates are ignored.
    - Additional sessions are appended.
    - Changed prices for an existing key abort the merge.
    - Existing rows are never silently overwritten.
    """

    existing_by_key = {
        history_key(row): dict(row)
        for row in existing_rows
    }

    observed_keys = set()
    conflicts = []
    new_rows = []
    unchanged_count = 0

    for observed_row in observed_rows:
        key = history_key(observed_row)

        date_text, session = key

        if not date_text:
            raise ValueError(
                "Observed history row has a blank date"
            )

        if key in observed_keys:
            raise ValueError(
                "Duplicate observed date/session key: "
                f"{key[0]} | {key[1] or 'Standard'}"
            )

        observed_keys.add(key)

        stored_row = existing_by_key.get(key)

        if stored_row is None:
            new_rows.append(dict(observed_row))
            continue

        row_conflicts = find_price_conflicts(
            stored_row,
            observed_row,
        )

        if row_conflicts:
            conflicts.extend(row_conflicts)
        else:
            unchanged_count += 1

    if conflicts:
        raise HistoryConflictError(conflicts)

    merged_rows = [
        *existing_by_key.values(),
        *new_rows,
    ]

    merged_rows.sort(key=history_sort_key)

    return MergeResult(
        rows=merged_rows,
        added_count=len(new_rows),
        unchanged_count=unchanged_count,
    )