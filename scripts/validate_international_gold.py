"""Validate an international gold-price CSV file.

The validator checks:

- Expected column names
- Valid UTC timestamps
- Dates matching timestamps
- Chronological ordering
- Duplicate timestamps
- Positive prices
- Troy-ounce to gram conversion
- Required GoldPrice.org attribution

This script only reads the CSV. It never modifies it.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_INPUT_PATH = (
    PROJECT_ROOT
    / "international_gold_31_day_preview.csv"
)

EXPECTED_FIELDS = [
    "timestamp_utc",
    "date",
    "price_usd_per_troy_ounce",
    "price_usd_per_gram",
    "source",
]

TROY_OUNCE_GRAMS = Decimal("31.1034768")

MAX_CONVERSION_DIFFERENCE = Decimal("0.000001")


def parse_args():
    """Read the optional CSV path from the command line."""

    parser = argparse.ArgumentParser(
        description=(
            "Validate international gold-price history."
        )
    )

    parser.add_argument(
        "path",
        nargs="?",
        default=str(DEFAULT_INPUT_PATH),
        help=(
            "CSV file to validate. Defaults to "
            "international_gold_31_day_preview.csv."
        ),
    )

    return parser.parse_args()


def parse_timestamp(value):
    """Convert an ISO UTC timestamp to a datetime."""

    timestamp_text = value.strip()

    if timestamp_text.endswith("Z"):
        timestamp_text = (
            timestamp_text[:-1] + "+00:00"
        )

    timestamp = datetime.fromisoformat(
        timestamp_text
    )

    if timestamp.tzinfo is None:
        raise ValueError(
            "Timestamp does not include a timezone."
        )

    return timestamp


def parse_positive_decimal(
    value,
    field_name,
):
    """Convert a price to a positive Decimal."""

    try:
        number = Decimal(
            value.strip().replace(",", "")
        )

    except InvalidOperation as exc:
        raise ValueError(
            f"{field_name} is not a valid number."
        ) from exc

    if number <= 0:
        raise ValueError(
            f"{field_name} must be greater than zero."
        )

    return number


def validate_file(path):
    """Validate one international gold-price CSV."""

    errors = []
    warnings = []

    if not path.exists():
        return [
            f"File does not exist: {path}"
        ], warnings, []

    with path.open(
        "r",
        encoding="utf-8",
        newline="",
    ) as file:
        reader = csv.DictReader(file)

        if reader.fieldnames != EXPECTED_FIELDS:
            errors.append(
                "Unexpected CSV columns. "
                f"Expected {EXPECTED_FIELDS}; "
                f"found {reader.fieldnames}."
            )

            return errors, warnings, []

        rows = list(reader)

    if not rows:
        errors.append(
            "The CSV contains no data records."
        )

        return errors, warnings, rows

    seen_timestamps = set()
    previous_timestamp = None

    for row_number, row in enumerate(
        rows,
        start=2,
    ):
        try:
            timestamp = parse_timestamp(
                row["timestamp_utc"]
            )

        except Exception as exc:
            errors.append(
                f"Row {row_number}: "
                f"invalid timestamp: {exc}"
            )

            continue

        if row["date"].strip() != (
            timestamp.date().isoformat()
        ):
            errors.append(
                f"Row {row_number}: date "
                "does not match timestamp."
            )

        if timestamp in seen_timestamps:
            errors.append(
                f"Row {row_number}: "
                "duplicate timestamp."
            )

        seen_timestamps.add(timestamp)

        if (
            previous_timestamp is not None
            and timestamp <= previous_timestamp
        ):
            errors.append(
                f"Row {row_number}: timestamps "
                "are not strictly increasing."
            )

        previous_timestamp = timestamp

        try:
            ounce_price = (
                parse_positive_decimal(
                    row[
                        "price_usd_per_troy_ounce"
                    ],
                    "Troy-ounce price",
                )
            )

            gram_price = (
                parse_positive_decimal(
                    row[
                        "price_usd_per_gram"
                    ],
                    "Gram price",
                )
            )

        except ValueError as exc:
            errors.append(
                f"Row {row_number}: {exc}"
            )

            continue

        expected_gram_price = (
            ounce_price / TROY_OUNCE_GRAMS
        )

        conversion_difference = abs(
            gram_price - expected_gram_price
        )

        if (
            conversion_difference
            > MAX_CONVERSION_DIFFERENCE
        ):
            errors.append(
                f"Row {row_number}: incorrect "
                "troy-ounce to gram conversion."
            )

        if row["source"].strip() != (
            "GoldPrice.org"
        ):
            errors.append(
                f"Row {row_number}: required "
                "GoldPrice.org attribution is missing."
            )

    return errors, warnings, rows


def main():
    """Run validation and print a readable report."""

    args = parse_args()
    path = Path(args.path).resolve()

    errors, warnings, rows = validate_file(
        path
    )

    print(
        "International gold data validation"
    )
    print(
        "----------------------------------"
    )
    print(f"File: {path}")
    print(f"Records: {len(rows)}")

    if rows:
        print(
            f"First date: {rows[0]['date']}"
        )

        print(
            f"Last date: {rows[-1]['date']}"
        )

    print(f"Errors: {len(errors)}")
    print(f"Warnings: {len(warnings)}")

    for error in errors:
        print(f"ERROR: {error}")

    for warning in warnings:
        print(f"WARNING: {warning}")

    if errors:
        print("Result: FAILED")
        return 1

    print("Result: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())