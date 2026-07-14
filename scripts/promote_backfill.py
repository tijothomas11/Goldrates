"""
Promote the validated KeralaGold backfill to permanent history.

Validated source:
    data/gold_rates_full_backfill.csv

Permanent destination:
    data/gold_rates_history.csv

This script replaces the permanent history with the complete
validated backfill. It does not attempt to merge the old 19-row
file because the full backfill already includes the current month.

Safety measures:
- validates the source schema;
- requires exactly 6,310 records;
- validates dates and amounts;
- validates every pavan-to-gram calculation;
- requires zero non-zero source differences;
- requires zero duplicate date/session keys;
- backs up the existing permanent history;
- writes through a temporary file;
- reopens and validates the written file;
- restores the backup if final validation fails.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import shutil
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"

SOURCE_PATH = DATA_DIR / "gold_rates_full_backfill.csv"
DESTINATION_PATH = DATA_DIR / "gold_rates_history.csv"

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

EXPECTED_RECORD_COUNT = 6310
EXPECTED_FIRST_DATE = "2009-11-01"
EXPECTED_LAST_DATE = "2026-07-14"

MIN_GRAM_PRICE = Decimal("500")
MAX_GRAM_PRICE = Decimal("50000")

MIN_PAVAN_PRICE = Decimal("4000")
MAX_PAVAN_PRICE = Decimal("400000")

SESSION_ORDER = {
    "": 0,
    "Early Morning": 1,
    "Morning": 2,
    "Forenoon": 3,
    "Noon": 4,
    "Afternoon": 5,
    "Evening": 6,
    "Night": 7,
}


def parse_decimal(
    value: str | None,
) -> Decimal | None:
    """Convert optional CSV text to a Decimal."""

    # A blank or missing value is treated as absent data.
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


def record_key(
    row: dict[str, str],
) -> tuple[str, str]:
    """Return the unique date-and-session key."""

    return (
        row.get("date", "").strip(),
        row.get("session", "").strip(),
    )


def record_sort_key(
    row: dict[str, str],
) -> tuple[object, ...]:
    """Sort records by date and chronological session."""

    date_text = row.get("date", "").strip()
    session = row.get("session", "").strip()

    return (
        date_text,
        SESSION_ORDER.get(session, 50),
        session,
    )


def load_csv(
    path: Path,
) -> tuple[list[str], list[dict[str, str]]]:
    """Load a CSV and return its fields and rows."""

    # The promotion script must only operate on valid CSV files.
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


def validate_rows(
    rows: list[dict[str, str]],
    *,
    require_exact_dataset: bool,
) -> list[str]:
    """Return all validation errors found in the rows."""

    # Ensure the backfill CSV is the exact expected dataset and has no data issues.
    errors: list[str] = []

    if require_exact_dataset:
        if len(rows) != EXPECTED_RECORD_COUNT:
            errors.append(
                "Unexpected record count: "
                f"{len(rows)}; expected "
                f"{EXPECTED_RECORD_COUNT}."
            )

    key_counts = Counter(
        record_key(row)
        for row in rows
    )

    duplicate_keys = [
        key
        for key, count in key_counts.items()
        if count > 1
    ]

    if duplicate_keys:
        errors.append(
            "Duplicate date/session keys found: "
            f"{len(duplicate_keys)}."
        )

        for date_text, session in duplicate_keys[:20]:
            errors.append(
                "Duplicate key: "
                f"{date_text} | "
                f"{session or 'Standard'}."
            )

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
            errors.append(
                f"Row {row_number}: date is blank."
            )
            continue

        try:
            date_value = dt.date.fromisoformat(
                date_text
            )
        except ValueError:
            errors.append(
                f"Row {row_number}: invalid date "
                f"{date_text!r}."
            )
            continue

        if date_value > dt.date.today():
            errors.append(
                f"Row {row_number}: future date "
                f"{date_text}."
            )

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
            errors.append(
                f"Row {row_number}: {exc}"
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

        if (
            published_gram is None
            and published_pavan is None
        ):
            errors.append(
                f"Row {row_number}: both published "
                "source prices are missing."
            )

        if published_gram is not None:
            if not (
                MIN_GRAM_PRICE
                <= published_gram
                <= MAX_GRAM_PRICE
            ):
                errors.append(
                    f"Row {row_number}: published gram "
                    "price is outside the validation "
                    f"range: {published_gram}."
                )

            if not gram_source_url:
                errors.append(
                    f"Row {row_number}: published gram "
                    "price has no source URL."
                )

        elif gram_source_url:
            errors.append(
                f"Row {row_number}: gram source URL "
                "exists while published gram is blank."
            )

        if published_pavan is not None:
            if not (
                MIN_PAVAN_PRICE
                <= published_pavan
                <= MAX_PAVAN_PRICE
            ):
                errors.append(
                    f"Row {row_number}: published pavan "
                    "price is outside the validation "
                    f"range: {published_pavan}."
                )

            if not pavan_source_url:
                errors.append(
                    f"Row {row_number}: published pavan "
                    "price has no source URL."
                )

        elif pavan_source_url:
            errors.append(
                f"Row {row_number}: pavan source URL "
                "exists while published pavan is blank."
            )

        expected_from_pavan = (
            published_pavan / Decimal("8")
            if published_pavan is not None
            else None
        )

        if gram_from_pavan != expected_from_pavan:
            errors.append(
                f"Row {row_number}: incorrect pavan "
                f"conversion for {date_text} | "
                f"{session or 'Standard'}; "
                f"stored={gram_from_pavan}, "
                f"expected={expected_from_pavan}."
            )

        expected_difference = (
            published_gram - expected_from_pavan
            if published_gram is not None
            and expected_from_pavan is not None
            else None
        )

        if stored_difference != expected_difference:
            errors.append(
                f"Row {row_number}: incorrect stored "
                f"difference for {date_text} | "
                f"{session or 'Standard'}; "
                f"stored={stored_difference}, "
                f"expected={expected_difference}."
            )

        if (
            expected_difference is not None
            and expected_difference != 0
        ):
            errors.append(
                f"Row {row_number}: published gram and "
                f"pavan/8 differ for {date_text} | "
                f"{session or 'Standard'}; "
                f"difference={expected_difference}."
            )

        expected_normalized = (
            published_gram
            if published_gram is not None
            else expected_from_pavan
        )

        if normalized_gram != expected_normalized:
            errors.append(
                f"Row {row_number}: incorrect "
                f"normalized gram value for "
                f"{date_text} | "
                f"{session or 'Standard'}; "
                f"stored={normalized_gram}, "
                f"expected={expected_normalized}."
            )

        if normalized_gram is None:
            errors.append(
                f"Row {row_number}: normalized gram "
                "price is missing."
            )

        elif not (
            MIN_GRAM_PRICE
            <= normalized_gram
            <= MAX_GRAM_PRICE
        ):
            errors.append(
                f"Row {row_number}: normalized gram "
                "price is outside the validation "
                f"range: {normalized_gram}."
            )

    if rows:
        dates = sorted(
            row.get("date", "").strip()
            for row in rows
            if row.get("date", "").strip()
        )

        if not dates:
            errors.append(
                "No valid dates were found."
            )

        elif require_exact_dataset:
            if dates[0] != EXPECTED_FIRST_DATE:
                errors.append(
                    "Unexpected first date: "
                    f"{dates[0]}; expected "
                    f"{EXPECTED_FIRST_DATE}."
                )

            if dates[-1] != EXPECTED_LAST_DATE:
                errors.append(
                    "Unexpected last date: "
                    f"{dates[-1]}; expected "
                    f"{EXPECTED_LAST_DATE}."
                )

    return errors


def sha256_file(path: Path) -> str:
    """Return the SHA-256 hash of a file."""

    # Hash the file contents to ensure the source does not change mid-promotion.
    digest = hashlib.sha256()

    with path.open("rb") as file:
        while True:
            chunk = file.read(
                1024 * 1024
            )

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def create_backup() -> Path | None:
    """Back up the existing permanent history file."""

    if not DESTINATION_PATH.exists():
        return None

    timestamp = dt.datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    backup_path = (
        DESTINATION_PATH.parent
        / (
            "gold_rates_history."
            f"before_full_backfill_{timestamp}.csv"
        )
    )

    shutil.copy2(
        DESTINATION_PATH,
        backup_path,
    )

    return backup_path


def write_csv_atomically(
    path: Path,
    rows: list[dict[str, str]],
) -> None:
    """Write the CSV through a temporary file."""

    # Write to a temp file first, then replace the destination.
    # This prevents partial files when something fails mid-write.

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = path.with_suffix(
        path.suffix + ".tmp"
    )

    try:
        with temporary_path.open(
            "w",
            newline="",
            encoding="utf-8",
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=EXPECTED_FIELDS,
            )

            writer.writeheader()
            writer.writerows(rows)
            file.flush()

        temporary_path.replace(path)

    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()

        raise


def restore_backup(
    backup_path: Path | None,
) -> None:
    """Restore the previous permanent history if available."""

    if backup_path is None:
        if DESTINATION_PATH.exists():
            DESTINATION_PATH.unlink()

        print(
            "The newly created permanent history "
            "was removed."
        )

        return

    shutil.copy2(
        backup_path,
        DESTINATION_PATH,
    )

    print(
        "The previous permanent history was "
        "restored from backup:"
    )
    print(f"  {backup_path}")


def print_errors(
    errors: list[str],
    *,
    limit: int = 30,
) -> None:
    """Print a limited number of validation errors."""

    for error in errors[:limit]:
        print(f"- {error}")

    remaining = len(errors) - limit

    if remaining > 0:
        print(
            f"- ... and {remaining} more errors."
        )


def main() -> int:
    print("Validating temporary backfill...")

    try:
        source_fields, source_rows = load_csv(
            SOURCE_PATH
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        print(
            "The permanent history was not modified."
        )
        return 1

    if source_fields != EXPECTED_FIELDS:
        print(
            "ERROR: the temporary backfill has an "
            "unexpected CSV schema."
        )
        print(f"Expected: {EXPECTED_FIELDS}")
        print(f"Found:    {source_fields}")
        print(
            "The permanent history was not modified."
        )
        return 1

    source_errors = validate_rows(
        source_rows,
        require_exact_dataset=True,
    )

    if source_errors:
        print("")
        print(
            "ERROR: temporary backfill validation "
            "failed."
        )
        print_errors(source_errors)
        print(
            "The permanent history was not modified."
        )
        return 1

    source_rows = sorted(
        source_rows,
        key=record_sort_key,
    )

    source_dates = sorted(
        row["date"]
        for row in source_rows
    )

    print(
        f"Validated backfill records: "
        f"{len(source_rows)}"
    )

    print(
        f"Backfill date range: "
        f"{source_dates[0]} to "
        f"{source_dates[-1]}"
    )

    source_hash_before = sha256_file(
        SOURCE_PATH
    )

    backup_path = create_backup()

    if backup_path is None:
        print(
            "No existing permanent history was "
            "found. A new permanent file will be "
            "created."
        )

    else:
        print(
            "Existing permanent history backed up to:"
        )
        print(f"  {backup_path}")

    print(
        "Replacing permanent history with the "
        "validated full backfill..."
    )

    try:
        write_csv_atomically(
            DESTINATION_PATH,
            source_rows,
        )

    except Exception as exc:
        print("")
        print(
            "ERROR: writing permanent history failed:"
        )
        print(f"  {exc}")

        restore_backup(backup_path)
        return 1

    print(
        "Reopening and validating the written "
        "permanent history..."
    )

    try:
        written_fields, written_rows = load_csv(
            DESTINATION_PATH
        )

    except Exception as exc:
        print("")
        print(
            "ERROR: the written permanent history "
            "could not be reopened:"
        )
        print(f"  {exc}")

        restore_backup(backup_path)
        return 1

    if written_fields != EXPECTED_FIELDS:
        print("")
        print(
            "ERROR: the written permanent history "
            "has an unexpected schema."
        )
        print(f"Expected: {EXPECTED_FIELDS}")
        print(f"Found:    {written_fields}")

        restore_backup(backup_path)
        return 1

    written_errors = validate_rows(
        written_rows,
        require_exact_dataset=True,
    )

    if written_errors:
        print("")
        print(
            "ERROR: final permanent-history "
            "validation failed."
        )
        print_errors(written_errors)

        restore_backup(backup_path)
        return 1

    if len(written_rows) != len(source_rows):
        print("")
        print(
            "ERROR: final record count differs from "
            "the validated source."
        )
        print(
            f"Source records: {len(source_rows)}"
        )
        print(
            f"Written records: {len(written_rows)}"
        )

        restore_backup(backup_path)
        return 1

    source_keys = {
        record_key(row)
        for row in source_rows
    }

    written_keys = {
        record_key(row)
        for row in written_rows
    }

    if source_keys != written_keys:
        print("")
        print(
            "ERROR: final date/session keys differ "
            "from the validated source."
        )

        restore_backup(backup_path)
        return 1

    written_dates = sorted(
        row["date"]
        for row in written_rows
    )

    source_hash_after = sha256_file(
        SOURCE_PATH
    )

    destination_hash = sha256_file(
        DESTINATION_PATH
    )

    if source_hash_before != source_hash_after:
        print("")
        print(
            "ERROR: the temporary backfill changed "
            "during promotion."
        )

        restore_backup(backup_path)
        return 1

    print("")
    print("Promotion completed successfully.")
    print(
        f"Permanent records: "
        f"{len(written_rows)}"
    )
    print(
        f"Permanent date range: "
        f"{written_dates[0]} to "
        f"{written_dates[-1]}"
    )
    print(
        f"Permanent file: "
        f"{DESTINATION_PATH}"
    )

    if backup_path is not None:
        print(
            "Previous permanent-history backup:"
        )
        print(f"  {backup_path}")

    print(
        f"Source SHA-256:      "
        f"{source_hash_after}"
    )
    print(
        f"Destination SHA-256: "
        f"{destination_hash}"
    )

    if source_hash_after == destination_hash:
        print(
            "Source and destination CSV files are "
            "byte-for-byte identical."
        )
    else:
        print(
            "Source and destination are logically "
            "identical, but their byte representation "
            "differs because the permanent output was "
            "rewritten in sorted order."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())