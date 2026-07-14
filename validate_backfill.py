"""
Validate the KeralaGold historical backfill.

This validator is read-only. It does not modify the backfill
CSV, progress CSV, permanent history, or source files.

Inputs:
    data/gold_rates_history.csv
    data/gold_rates_backfill_progress.csv
    data/gold_rates_backfill_errors.csv

Output:
    data/gold_rates_validation_report.txt
"""

from __future__ import annotations

import csv
import datetime as dt
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path


BACKFILL_PATH = Path(
    "data/gold_rates_history.csv"
)

PROGRESS_PATH = Path(
    "data/gold_rates_backfill_progress.csv"
)

ERROR_LOG_PATH = Path(
    "data/gold_rates_backfill_errors.csv"
)

REPORT_PATH = Path(
    "data/gold_rates_validation_report.txt"
)


EXPECTED_FIELDS = [
    "date",
    "session",
    "price_22k_1g_published",
    "price_22k_8g_published",
    "price_22k_1g_from_pavan",
    "difference_22k",
    "normalized_price_22k_1g",
    "gram_source_url",
    "pavan_source_url",
]


KNOWN_SESSIONS = {
    "",
    "Early Morning",
    "Morning",
    "Forenoon",
    "Noon",
    "Afternoon",
    "Evening",
    "Night",
}


# These limits are intentionally broad. They are meant to detect
# parser errors such as a gram price of 10, not ordinary market
# fluctuations.
MIN_GRAM_PRICE = Decimal("500")
MAX_GRAM_PRICE = Decimal("50000")

MIN_PAVAN_PRICE = Decimal("4000")
MAX_PAVAN_PRICE = Decimal("400000")

# Consecutive normalized prices changing by more than this
# percentage are reported for manual review.
LARGE_CHANGE_PERCENT = Decimal("10")


def parse_decimal(
    value: str | None,
) -> Decimal | None:
    """
    Convert optional CSV text to Decimal.

    Blank values are returned as None.
    """

    if value is None:
        return None

    cleaned = value.strip().replace(",", "")

    if not cleaned:
        return None

    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(
            f"Invalid decimal value: {value!r}"
        ) from exc


def parse_date(value: str) -> dt.date:
    """Parse an ISO date such as 2020-07-09."""

    try:
        return dt.date.fromisoformat(
            value.strip()
        )
    except ValueError as exc:
        raise ValueError(
            f"Invalid date value: {value!r}"
        ) from exc


def load_csv(
    path: Path,
) -> tuple[list[str], list[dict[str, str]]]:
    """Load one CSV file."""

    if not path.exists():
        raise FileNotFoundError(
            f"Required file was not found: {path}"
        )

    with path.open(
        newline="",
        encoding="utf-8",
    ) as file:
        reader = csv.DictReader(file)
        fields = reader.fieldnames or []
        rows = list(reader)

    return fields, rows


def add_row_issue(
    issues: list[str],
    row_number: int,
    message: str,
) -> None:
    """Record an issue associated with one CSV row."""

    issues.append(
        f"Row {row_number}: {message}"
    )


def validate_backfill_rows(
    rows: list[dict[str, str]],
) -> dict[str, object]:
    """Validate every row in the main backfill CSV."""

    errors: list[str] = []
    warnings: list[str] = []

    parsed_rows: list[dict[str, object]] = []

    key_counts: Counter[
        tuple[str, str]
    ] = Counter()

    month_counts: Counter[str] = Counter()
    session_counts: Counter[str] = Counter()

    published_gram_count = 0
    published_pavan_count = 0
    both_sources_count = 0
    pavan_only_count = 0
    gram_only_count = 0
    exact_match_count = 0
    non_zero_difference_count = 0

    for row_number, row in enumerate(
        rows,
        start=2,
    ):
        date_text = row.get(
            "date",
            "",
        ).strip()

        session = row.get(
            "session",
            "",
        ).strip()

        if not date_text:
            add_row_issue(
                errors,
                row_number,
                "date is blank.",
            )
            continue

        try:
            date_value = parse_date(date_text)
        except ValueError as exc:
            add_row_issue(
                errors,
                row_number,
                str(exc),
            )
            continue

        if date_value > dt.date.today():
            add_row_issue(
                errors,
                row_number,
                f"future date found: {date_value}.",
            )

        if date_value < dt.date(
            2009,
            11,
            1,
        ):
            add_row_issue(
                warnings,
                row_number,
                "date predates the expected daily "
                f"archive: {date_value}.",
            )

        if session not in KNOWN_SESSIONS:
            add_row_issue(
                warnings,
                row_number,
                f"unrecognized session: {session!r}.",
            )

        record_key = (
            date_text,
            session,
        )

        key_counts[record_key] += 1

        month_counts[
            date_value.strftime("%Y-%m")
        ] += 1

        session_counts[
            session or "Standard"
        ] += 1

        try:
            published_gram = parse_decimal(
                row.get(
                    "price_22k_1g_published"
                )
            )

            published_pavan = parse_decimal(
                row.get(
                    "price_22k_8g_published"
                )
            )

            gram_from_pavan = parse_decimal(
                row.get(
                    "price_22k_1g_from_pavan"
                )
            )

            stored_difference = parse_decimal(
                row.get(
                    "difference_22k"
                )
            )

            normalized_gram = parse_decimal(
                row.get(
                    "normalized_price_22k_1g"
                )
            )

        except ValueError as exc:
            add_row_issue(
                errors,
                row_number,
                str(exc),
            )
            continue

        gram_source_url = row.get(
            "gram_source_url",
            "",
        ).strip()

        pavan_source_url = row.get(
            "pavan_source_url",
            "",
        ).strip()

        if published_gram is not None:
            published_gram_count += 1

        if published_pavan is not None:
            published_pavan_count += 1

        if (
            published_gram is not None
            and published_pavan is not None
        ):
            both_sources_count += 1

        elif published_pavan is not None:
            pavan_only_count += 1

        elif published_gram is not None:
            gram_only_count += 1

        else:
            add_row_issue(
                errors,
                row_number,
                "both published gram and published "
                "pavan prices are missing.",
            )

        # Validate published gram price and source URL.
        if published_gram is not None:
            if published_gram <= 0:
                add_row_issue(
                    errors,
                    row_number,
                    "published gram price is zero "
                    "or negative.",
                )

            if not (
                MIN_GRAM_PRICE
                <= published_gram
                <= MAX_GRAM_PRICE
            ):
                add_row_issue(
                    errors,
                    row_number,
                    "published gram price is outside "
                    "the broad historical range: "
                    f"{published_gram}.",
                )

            if not gram_source_url:
                add_row_issue(
                    errors,
                    row_number,
                    "published gram price has no "
                    "gram source URL.",
                )

        elif gram_source_url:
            add_row_issue(
                warnings,
                row_number,
                "gram source URL exists while the "
                "published gram price is blank.",
            )

        # Validate published pavan price and source URL.
        if published_pavan is not None:
            if published_pavan <= 0:
                add_row_issue(
                    errors,
                    row_number,
                    "published pavan price is zero "
                    "or negative.",
                )

            if not (
                MIN_PAVAN_PRICE
                <= published_pavan
                <= MAX_PAVAN_PRICE
            ):
                add_row_issue(
                    errors,
                    row_number,
                    "published pavan price is outside "
                    "the broad historical range: "
                    f"{published_pavan}.",
                )

            if not pavan_source_url:
                add_row_issue(
                    errors,
                    row_number,
                    "published pavan price has no "
                    "pavan source URL.",
                )

        elif pavan_source_url:
            add_row_issue(
                warnings,
                row_number,
                "pavan source URL exists while the "
                "published pavan price is blank.",
            )

        # Recalculate the exact gram value from pavan.
        expected_gram_from_pavan = None

        if published_pavan is not None:
            expected_gram_from_pavan = (
                published_pavan / Decimal("8")
            )

        if expected_gram_from_pavan is None:
            if gram_from_pavan is not None:
                add_row_issue(
                    errors,
                    row_number,
                    "calculated gram value exists "
                    "while published pavan is missing.",
                )

        elif gram_from_pavan != expected_gram_from_pavan:
            add_row_issue(
                errors,
                row_number,
                "incorrect pavan conversion: "
                f"stored={gram_from_pavan}, "
                f"expected={expected_gram_from_pavan}.",
            )

        # Recalculate the difference between the two sources.
        expected_difference = None

        if (
            published_gram is not None
            and expected_gram_from_pavan
            is not None
        ):
            expected_difference = (
                published_gram
                - expected_gram_from_pavan
            )

        if expected_difference is None:
            if stored_difference is not None:
                add_row_issue(
                    errors,
                    row_number,
                    "difference exists although both "
                    "published sources are not present.",
                )

        elif stored_difference != expected_difference:
            add_row_issue(
                errors,
                row_number,
                "incorrect stored difference: "
                f"stored={stored_difference}, "
                f"expected={expected_difference}.",
            )

        if expected_difference is not None:
            if expected_difference == 0:
                exact_match_count += 1
            else:
                non_zero_difference_count += 1

                add_row_issue(
                    warnings,
                    row_number,
                    "published gram and pavan/8 differ: "
                    f"gram={published_gram}, "
                    "pavan/8="
                    f"{expected_gram_from_pavan}, "
                    "difference="
                    f"{expected_difference}.",
                )

        # The normalized rate should prefer the directly
        # published gram rate. If no gram rate exists, it should
        # use the value calculated from pavan.
        expected_normalized = (
            published_gram
            if published_gram is not None
            else expected_gram_from_pavan
        )

        if normalized_gram is None:
            add_row_issue(
                errors,
                row_number,
                "normalized gram price is missing.",
            )

        elif normalized_gram != expected_normalized:
            add_row_issue(
                errors,
                row_number,
                "incorrect normalized gram price: "
                f"stored={normalized_gram}, "
                f"expected={expected_normalized}.",
            )

        if normalized_gram is not None:
            if not (
                MIN_GRAM_PRICE
                <= normalized_gram
                <= MAX_GRAM_PRICE
            ):
                add_row_issue(
                    errors,
                    row_number,
                    "normalized gram price is outside "
                    "the broad historical range: "
                    f"{normalized_gram}.",
                )

        parsed_rows.append(
            {
                "row_number": row_number,
                "date": date_value,
                "date_text": date_text,
                "session": session,
                "normalized": normalized_gram,
            }
        )

    # Check duplicate date-and-session combinations.
    duplicate_keys = {
        key: count
        for key, count in key_counts.items()
        if count > 1
    }

    for key, count in sorted(
        duplicate_keys.items()
    ):
        date_text, session = key

        errors.append(
            "Duplicate date/session key: "
            f"{date_text} | "
            f"{session or 'Standard'} appears "
            f"{count} times."
        )

    # Sort observations using date and a sensible session order.
    session_order = {
        "": 0,
        "Early Morning": 1,
        "Morning": 2,
        "Forenoon": 3,
        "Noon": 4,
        "Afternoon": 5,
        "Evening": 6,
        "Night": 7,
    }

    parsed_rows.sort(
        key=lambda item: (
            item["date"],
            session_order.get(
                str(item["session"]),
                50,
            ),
            str(item["session"]),
        )
    )

    # Report unusually large consecutive changes.
    large_changes: list[str] = []

    previous_value: Decimal | None = None
    previous_label = ""

    for item in parsed_rows:
        current_value = item["normalized"]

        if not isinstance(
            current_value,
            Decimal,
        ):
            continue

        current_label = (
            f"{item['date_text']} | "
            f"{item['session'] or 'Standard'}"
        )

        if (
            previous_value is not None
            and previous_value != 0
        ):
            percentage_change = (
                abs(
                    current_value
                    - previous_value
                )
                / previous_value
                * Decimal("100")
            )

            if (
                percentage_change
                > LARGE_CHANGE_PERCENT
            ):
                rounded_percentage = (
                    percentage_change.quantize(
                        Decimal("0.01")
                    )
                )

                large_changes.append(
                    f"{previous_label} -> "
                    f"{current_label}: "
                    f"{previous_value} -> "
                    f"{current_value} "
                    f"({rounded_percentage}%)"
                )

        previous_value = current_value
        previous_label = current_label

    return {
        "errors": errors,
        "warnings": warnings,
        "parsed_rows": parsed_rows,
        "duplicate_keys": duplicate_keys,
        "month_counts": month_counts,
        "session_counts": session_counts,
        "published_gram_count": (
            published_gram_count
        ),
        "published_pavan_count": (
            published_pavan_count
        ),
        "both_sources_count": (
            both_sources_count
        ),
        "pavan_only_count": pavan_only_count,
        "gram_only_count": gram_only_count,
        "exact_match_count": exact_match_count,
        "non_zero_difference_count": (
            non_zero_difference_count
        ),
        "large_changes": large_changes,
    }


def validate_progress_file() -> dict[str, object]:
    """Check the backfill progress file."""

    if not PROGRESS_PATH.exists():
        return {
            "exists": False,
            "completed": [],
            "duplicate_months": [],
            "invalid_status_rows": [],
        }

    _, rows = load_csv(PROGRESS_PATH)

    completed_months: list[str] = []
    invalid_status_rows: list[str] = []

    for row_number, row in enumerate(
        rows,
        start=2,
    ):
        month = row.get(
            "month",
            "",
        ).strip()

        status = row.get(
            "status",
            "",
        ).strip()

        if status == "completed" and month:
            completed_months.append(month)
        else:
            invalid_status_rows.append(
                f"Row {row_number}: "
                f"month={month!r}, "
                f"status={status!r}"
            )

    month_counter = Counter(
        completed_months
    )

    duplicate_months = sorted(
        month
        for month, count
        in month_counter.items()
        if count > 1
    )

    return {
        "exists": True,
        "completed": sorted(
            set(completed_months)
        ),
        "duplicate_months": (
            duplicate_months
        ),
        "invalid_status_rows": (
            invalid_status_rows
        ),
    }


def load_error_log() -> list[dict[str, str]]:
    """Load the optional error log."""

    if not ERROR_LOG_PATH.exists():
        return []

    _, rows = load_csv(ERROR_LOG_PATH)
    return rows


def add_heading(
    lines: list[str],
    title: str,
) -> None:
    """Add a section heading to the report."""

    lines.append("")
    lines.append(title)
    lines.append("=" * len(title))


def add_limited_list(
    lines: list[str],
    items: list[str],
    limit: int = 50,
) -> None:
    """Add a limited number of report entries."""

    if not items:
        lines.append("None")
        return

    for item in items[:limit]:
        lines.append(f"- {item}")

    remaining = len(items) - limit

    if remaining > 0:
        lines.append(
            f"- ... and {remaining} more."
        )


def build_report(
    rows: list[dict[str, str]],
    results: dict[str, object],
    progress: dict[str, object],
    error_log: list[dict[str, str]],
) -> str:
    """Build a human-readable validation report."""

    errors = results["errors"]
    warnings = results["warnings"]
    parsed_rows = results["parsed_rows"]
    month_counts = results["month_counts"]
    session_counts = results["session_counts"]
    large_changes = results["large_changes"]

    assert isinstance(errors, list)
    assert isinstance(warnings, list)
    assert isinstance(parsed_rows, list)
    assert isinstance(month_counts, Counter)
    assert isinstance(session_counts, Counter)
    assert isinstance(large_changes, list)

    lines: list[str] = []

    lines.append(
        "KeralaGold Backfill Validation Report"
    )
    lines.append(
        "====================================="
    )

    generated_at = dt.datetime.now(
        dt.timezone.utc
    ).isoformat()

    lines.append(f"Generated: {generated_at}")
    lines.append(f"Input: {BACKFILL_PATH}")
    lines.append(f"CSV records: {len(rows)}")
    lines.append(
        f"Parsed records: {len(parsed_rows)}"
    )

    if parsed_rows:
        first_date = min(
            item["date"]
            for item in parsed_rows
        )

        last_date = max(
            item["date"]
            for item in parsed_rows
        )

        lines.append(
            f"Date range: {first_date} "
            f"to {last_date}"
        )

    add_heading(
        lines,
        "Overall result",
    )

    completed_months = progress[
        "completed"
    ]

    if errors:
        result_text = (
            "FAILED: data-integrity errors "
            "were found."
        )
    elif (
        progress["exists"]
        and len(completed_months) != 200
    ):
        result_text = (
            "INCOMPLETE: row calculations are "
            "valid, but not all 200 archive "
            "months are marked complete."
        )
    else:
        result_text = (
            "PASSED: no structural or calculation "
            "errors were detected."
        )

    lines.append(result_text)
    lines.append(f"Errors: {len(errors)}")
    lines.append(f"Warnings: {len(warnings)}")
    lines.append(
        "Backfill error-log entries: "
        f"{len(error_log)}"
    )

    add_heading(
        lines,
        "Source coverage",
    )

    lines.append(
        "Published gram records: "
        f"{results['published_gram_count']}"
    )

    lines.append(
        "Published pavan records: "
        f"{results['published_pavan_count']}"
    )

    lines.append(
        "Records with both sources: "
        f"{results['both_sources_count']}"
    )

    lines.append(
        "Pavan-only records: "
        f"{results['pavan_only_count']}"
    )

    lines.append(
        "Gram-only records: "
        f"{results['gram_only_count']}"
    )

    lines.append(
        "Exact gram versus pavan/8 matches: "
        f"{results['exact_match_count']}"
    )

    lines.append(
        "Non-zero gram/pavan differences: "
        f"{results['non_zero_difference_count']}"
    )

    add_heading(
        lines,
        "Archive completion",
    )

    lines.append(
        f"Progress file exists: "
        f"{progress['exists']}"
    )

    lines.append(
        "Completed archive months: "
        f"{len(completed_months)} / 200"
    )

    if completed_months:
        lines.append(
            f"First completed month: "
            f"{completed_months[0]}"
        )

        lines.append(
            f"Last completed month: "
            f"{completed_months[-1]}"
        )

    lines.append(
        "Duplicate progress months: "
        f"{len(progress['duplicate_months'])}"
    )

    add_limited_list(
        lines,
        progress["duplicate_months"],
    )

    lines.append(
        "Invalid progress rows: "
        f"{len(progress['invalid_status_rows'])}"
    )

    add_limited_list(
        lines,
        progress["invalid_status_rows"],
    )

    add_heading(
        lines,
        "Session counts",
    )

    for session, count in sorted(
        session_counts.items()
    ):
        lines.append(
            f"{session}: {count}"
        )

    add_heading(
        lines,
        "Records by month",
    )

    for month, count in sorted(
        month_counts.items()
    ):
        lines.append(
            f"{month}: {count}"
        )

    add_heading(
        lines,
        "Data-integrity errors",
    )

    add_limited_list(
        lines,
        errors,
        limit=100,
    )

    add_heading(
        lines,
        "Warnings",
    )

    add_limited_list(
        lines,
        warnings,
        limit=100,
    )

    add_heading(
        lines,
        "Large consecutive price changes",
    )

    lines.append(
        "These are review warnings, not "
        "automatic data errors."
    )

    add_limited_list(
        lines,
        large_changes,
        limit=100,
    )

    add_heading(
        lines,
        "Backfill error log",
    )

    if not error_log:
        lines.append("None")
    else:
        for row in error_log[:100]:
            lines.append(
                "- "
                f"{row.get('month', '')} | "
                f"{row.get('source_type', '')} | "
                f"{row.get('url', '')} | "
                f"{row.get('error', '')}"
            )

        remaining = len(error_log) - 100

        if remaining > 0:
            lines.append(
                f"- ... and {remaining} more."
            )

    return "\n".join(lines) + "\n"


def main() -> int:
    try:
        fieldnames, rows = load_csv(
            BACKFILL_PATH
        )
    except FileNotFoundError as exc:
        print(exc)
        print(
            "Run backfill_history.py before "
            "running this validator."
        )
        return 1

    if fieldnames != EXPECTED_FIELDS:
        print(
            "The backfill CSV has unexpected "
            "columns."
        )

        print("Expected:")
        print(EXPECTED_FIELDS)

        print("Found:")
        print(fieldnames)

        return 1

    results = validate_backfill_rows(
        rows
    )

    progress = validate_progress_file()

    error_log = load_error_log()

    report_text = build_report(
        rows,
        results,
        progress,
        error_log,
    )

    REPORT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = REPORT_PATH.with_suffix(
        ".txt.tmp"
    )

    temporary_path.write_text(
        report_text,
        encoding="utf-8",
    )

    temporary_path.replace(
        REPORT_PATH
    )

    errors = results["errors"]
    warnings = results["warnings"]
    completed_months = progress[
        "completed"
    ]

    print("Validation completed.")
    print(f"Records checked: {len(rows)}")
    print(f"Errors: {len(errors)}")
    print(f"Warnings: {len(warnings)}")

    print(
        "Completed archive months: "
        f"{len(completed_months)} / 200"
    )

    print(
        "Non-zero gram/pavan differences: "
        f"{results['non_zero_difference_count']}"
    )

    print(
        "Gram-only records: "
        f"{results['gram_only_count']}"
    )

    print(
        "Pavan-only records: "
        f"{results['pavan_only_count']}"
    )

    print(
        f"Report: {REPORT_PATH.resolve()}"
    )

    if errors:
        print("")
        print(
            "Result: FAILED. Review the "
            "data-integrity errors before merging."
        )
        return 1

    if (
        progress["exists"]
        and len(completed_months) != 200
    ):
        print("")
        print(
            "Result: INCOMPLETE. The rows checked "
            "are valid, but all 200 archive months "
            "are not marked complete."
        )
        return 2

    print("")
    print(
        "Result: PASSED. No structural or "
        "calculation errors were detected."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())