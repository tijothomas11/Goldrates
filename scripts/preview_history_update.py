"""Preview updates to permanent gold-rate history.

This script is read-only with respect to permanent history.

It:
- fetches the current gram and pavan pages;
- parses current-month observations;
- combines matching observations;
- compares them with permanent history;
- reports new, unchanged, and conflicting observations;
- writes preview and conflict reports.

It never modifies data/gold_rates_history.csv.
"""

import csv
import datetime as dt
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from history_update import (  # noqa: E402
    HistoryConflictError,
    merge_history_rows,
)

from backfill_history import (  # noqa: E402
    BASE_URL,
    OUTPUT_FIELDS,
    combine_records,
    fetch_with_retries,
    validate_month_records,
)


PERMANENT_HISTORY_PATH = (
    DATA_DIR / "gold_rates_history.csv"
)

PREVIEW_PATH = (
    DATA_DIR / "gold_rate_update_preview.csv"
)

CONFLICT_PATH = (
    DATA_DIR / "gold_rate_update_conflicts.csv"
)

GRAM_URL = (
    BASE_URL
    + "kerala-gold-rate-per-gram.htm"
)

PAVAN_URL = (
    BASE_URL
    + "daily-gold-prices.htm"
)

CONFLICT_FIELDS = [
    "detected_at",
    "date",
    "session",
    "field",
    "stored_value",
    "observed_value",
]


def load_permanent_history():
    """Load and verify the permanent-history CSV."""

    if not PERMANENT_HISTORY_PATH.exists():
        raise FileNotFoundError(
            "Permanent history was not found: "
            f"{PERMANENT_HISTORY_PATH}"
        )

    with PERMANENT_HISTORY_PATH.open(
        "r",
        encoding="utf-8",
        newline="",
    ) as file:
        reader = csv.DictReader(file)

        if reader.fieldnames != OUTPUT_FIELDS:
            raise ValueError(
                "Permanent history has an unexpected "
                "CSV schema."
            )

        return list(reader)


def write_csv(path, fieldnames, rows):
    """Write a report atomically."""

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = path.with_suffix(
        path.suffix + ".tmp"
    )

    with temporary_path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )

        writer.writeheader()
        writer.writerows(rows)

    temporary_path.replace(path)


def fetch_current_observations():
    """Fetch and combine current-month observations."""

    today = dt.date.today()

    print("Fetching current gram page...")
    gram_records = fetch_with_retries(
        GRAM_URL,
        missing_is_allowed=False,
    )

    print("Fetching current pavan page...")
    pavan_records = fetch_with_retries(
        PAVAN_URL,
        missing_is_allowed=False,
    )

    gram_records = validate_month_records(
        gram_records,
        today.year,
        today.month,
        "current gram",
    )

    pavan_records = validate_month_records(
        pavan_records,
        today.year,
        today.month,
        "current pavan",
    )

    combined = combine_records(
        gram_records,
        pavan_records,
        GRAM_URL,
        PAVAN_URL,
    )

    return (
        gram_records,
        pavan_records,
        combined,
    )


def write_conflicts(conflicts):
    """Write immutable-history conflicts for review."""

    detected_at = dt.datetime.now(
        dt.timezone.utc
    ).isoformat()

    rows = []

    for conflict in conflicts:
        rows.append(
            {
                "detected_at": detected_at,
                "date": conflict.date,
                "session": conflict.session,
                "field": conflict.field,
                "stored_value": (
                    conflict.stored_value
                ),
                "observed_value": (
                    conflict.observed_value
                ),
            }
        )

    write_csv(
        CONFLICT_PATH,
        CONFLICT_FIELDS,
        rows,
    )


def main():
    print(
        "Previewing permanent-history update."
    )
    print(
        "Permanent history will not be modified."
    )
    print("")

    try:
        existing_rows = load_permanent_history()

        (
            gram_records,
            pavan_records,
            observed_rows,
        ) = fetch_current_observations()

    except Exception as exc:
        print(f"Preview failed: {exc}")
        return 1

    write_csv(
        PREVIEW_PATH,
        OUTPUT_FIELDS,
        observed_rows,
    )

    print("")
    print("Live-page observations")
    print("----------------------")
    print(
        f"Gram records: {len(gram_records)}"
    )
    print(
        f"Pavan records: {len(pavan_records)}"
    )
    print(
        f"Combined records: {len(observed_rows)}"
    )

    try:
        result = merge_history_rows(
            existing_rows,
            observed_rows,
        )

    except HistoryConflictError as exc:
        write_conflicts(exc.conflicts)

        print("")
        print("UPDATE CONFLICT DETECTED")
        print("------------------------")
        print(
            f"Conflicting fields: "
            f"{len(exc.conflicts)}"
        )

        for conflict in exc.conflicts[:20]:
            print(
                f"{conflict.date} | "
                f"{conflict.session or 'Standard'} | "
                f"{conflict.field} | "
                f"stored={conflict.stored_value} | "
                f"observed={conflict.observed_value}"
            )

        remaining = len(exc.conflicts) - 20

        if remaining > 0:
            print(
                f"... and {remaining} more conflicts."
            )

        print("")
        print(
            "Permanent history was not modified."
        )
        print(
            f"Conflict report: {CONFLICT_PATH}"
        )
        print(
            f"Observed preview: {PREVIEW_PATH}"
        )

        return 2

    except Exception as exc:
        print("")
        print(f"Comparison failed: {exc}")
        print(
            "Permanent history was not modified."
        )
        return 1

    new_count = result.added_count
    unchanged_count = result.unchanged_count

    print("")
    print("Comparison result")
    print("-----------------")
    print(
        f"Permanent records: "
        f"{len(existing_rows)}"
    )
    print(
        f"Existing unchanged: "
        f"{unchanged_count}"
    )
    print(
        f"New observations: {new_count}"
    )
    print("Conflicts: 0")
    print(
        f"Records after proposed merge: "
        f"{len(result.rows)}"
    )

    if new_count:
        existing_keys = {
            (
                row["date"].strip(),
                row["session"].strip(),
            )
            for row in existing_rows
        }

        print("")
        print("New observations")
        print("----------------")

        for row in observed_rows:
            key = (
                row["date"].strip(),
                row["session"].strip(),
            )

            if key not in existing_keys:
                print(
                    f"{row['date']} | "
                    f"{row['session'] or 'Standard'} | "
                    f"gram="
                    f"{row['normalized_price_22k_1g']}"
                )

    print("")
    print(
        "Preview completed successfully."
    )
    print(
        "Permanent history was not modified."
    )
    print(
        f"Observed preview: {PREVIEW_PATH}"
    )

    if CONFLICT_PATH.exists():
        CONFLICT_PATH.unlink()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())