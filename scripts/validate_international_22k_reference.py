"""Validate the international-derived 22K INR reference dataset.

This validator is read-only. It checks the output schema,
timestamps, exchange-rate dates, source attribution, and
the mathematical 24K and 22K reference calculations.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_INPUT_PATH = (
    PROJECT_ROOT
    / "international_gold_22k_inr_preview.csv"
)

EXPECTED_FIELDS = [
    "gold_timestamp_utc",
    "gold_date",
    "fx_date",
    "fx_gap_days",
    "usd_per_troy_ounce",
    "usd_inr",
    "reference_24k_inr_per_gram",
    "reference_22k_inr_per_gram",
    "gold_source",
    "fx_source",
    "fx_series",
]

TROY_OUNCE_GRAMS = Decimal("31.1034768")

PURITY_FACTOR_22K = (
    Decimal("22") / Decimal("24")
)

MAX_FX_GAP_DAYS = 7

# Derived values are stored with four decimal places.
CALCULATION_TOLERANCE = Decimal("0.0001")

EXPECTED_GOLD_SOURCE = "GoldPrice.org"

EXPECTED_FX_SOURCE = (
    "Federal Reserve Board via FRED"
)

EXPECTED_FX_SERIES = "DEXINUS"


def parse_args() -> argparse.Namespace:
    """Read the optional input path."""

    parser = argparse.ArgumentParser(
        description=(
            "Validate an international-derived "
            "22K INR reference CSV."
        )
    )

    parser.add_argument(
        "path",
        nargs="?",
        default=str(DEFAULT_INPUT_PATH),
        help=(
            "Derived CSV to validate. Defaults to "
            "international_gold_22k_inr_preview.csv."
        ),
    )

    return parser.parse_args()


def parse_positive_decimal(
    value: str,
    field_name: str,
) -> Decimal:
    """Parse a positive finite decimal value."""

    try:
        number = Decimal(value.strip())

    except InvalidOperation as exc:
        raise ValueError(
            f"{field_name} is not a valid number."
        ) from exc

    if not number.is_finite():
        raise ValueError(
            f"{field_name} must be finite."
        )

    if number <= 0:
        raise ValueError(
            f"{field_name} must be greater than zero."
        )

    return number


def validate_file(
    path: Path,
) -> tuple[
    list[str],
    list[str],
    list[dict[str, str]],
]:
    """Validate one derived-reference CSV."""

    errors: list[str] = []
    warnings: list[str] = []

    if not path.exists():
        errors.append(
            f"File does not exist: {path}"
        )

        return errors, warnings, []

    try:
        with path.open(
            "r",
            encoding="utf-8-sig",
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

    except OSError as exc:
        errors.append(
            f"Could not read file: {exc}"
        )

        return errors, warnings, []

    if not rows:
        errors.append(
            "The CSV contains no derived records."
        )

        return errors, warnings, rows

    previous_timestamp: datetime | None = None
    seen_timestamps: set[str] = set()

    for row_number, row in enumerate(
        rows,
        start=2,
    ):
        try:
            timestamp_text = row[
                "gold_timestamp_utc"
            ].strip()

            timestamp = datetime.fromisoformat(
                timestamp_text.replace(
                    "Z",
                    "+00:00",
                )
            )

            gold_date = date.fromisoformat(
                row["gold_date"].strip()
            )

            fx_date = date.fromisoformat(
                row["fx_date"].strip()
            )

            fx_gap_days = int(
                row["fx_gap_days"].strip()
            )

            usd_per_ounce = (
                parse_positive_decimal(
                    row["usd_per_troy_ounce"],
                    "USD per troy ounce",
                )
            )

            usd_inr = parse_positive_decimal(
                row["usd_inr"],
                "USD/INR",
            )

            stored_24k = (
                parse_positive_decimal(
                    row[
                        "reference_24k_inr_per_gram"
                    ],
                    "24K INR reference",
                )
            )

            stored_22k = (
                parse_positive_decimal(
                    row[
                        "reference_22k_inr_per_gram"
                    ],
                    "22K INR reference",
                )
            )

        except Exception as exc:
            errors.append(
                f"Row {row_number}: {exc}"
            )

            continue

        if timestamp.date() != gold_date:
            errors.append(
                f"Row {row_number}: gold date does "
                "not match the UTC timestamp."
            )

        if timestamp_text in seen_timestamps:
            errors.append(
                f"Row {row_number}: duplicate "
                "gold timestamp."
            )

        seen_timestamps.add(timestamp_text)

        if (
            previous_timestamp is not None
            and timestamp <= previous_timestamp
        ):
            errors.append(
                f"Row {row_number}: timestamps are "
                "not strictly increasing."
            )

        previous_timestamp = timestamp

        if fx_date > gold_date:
            errors.append(
                f"Row {row_number}: future FX rate "
                "was used."
            )

        calculated_gap = (
            gold_date - fx_date
        ).days

        # The stored FX gap should match the actual calendar-day difference
        # between the gold observation and the chosen FX date.
        if fx_gap_days != calculated_gap:
            errors.append(
                f"Row {row_number}: stored FX gap "
                "does not match the dates."
            )

        if not (
            0 <= fx_gap_days <= MAX_FX_GAP_DAYS
        ):
            errors.append(
                f"Row {row_number}: FX gap is "
                f"outside 0 to {MAX_FX_GAP_DAYS} days."
            )

        calculated_24k = (
            usd_per_ounce
            / TROY_OUNCE_GRAMS
            * usd_inr
        )

        calculated_22k = (
            calculated_24k
            * PURITY_FACTOR_22K
        )

        if (
            abs(stored_24k - calculated_24k)
            > CALCULATION_TOLERANCE
        ):
            errors.append(
                f"Row {row_number}: incorrect "
                "24K reference calculation."
            )

        if (
            abs(stored_22k - calculated_22k)
            > CALCULATION_TOLERANCE
        ):
            errors.append(
                f"Row {row_number}: incorrect "
                "22K reference calculation."
            )

        if (
            row["gold_source"].strip()
            != EXPECTED_GOLD_SOURCE
        ):
            errors.append(
                f"Row {row_number}: unexpected "
                "gold source."
            )

        if (
            row["fx_source"].strip()
            != EXPECTED_FX_SOURCE
        ):
            errors.append(
                f"Row {row_number}: unexpected "
                "FX source."
            )

        if (
            row["fx_series"].strip()
            != EXPECTED_FX_SERIES
        ):
            errors.append(
                f"Row {row_number}: unexpected "
                "FX series."
            )

    return errors, warnings, rows


def main() -> int:
    """Run validation and print a report."""

    args = parse_args()
    path = Path(args.path).resolve()

    errors, warnings, rows = validate_file(
        path
    )

    print(
        "International 22K INR reference validation"
    )
    print(
        "-----------------------------------------"
    )
    print(f"File: {path}")
    print(f"Records: {len(rows)}")

    if rows:
        print(
            "First gold date:",
            rows[0]["gold_date"],
        )

        print(
            "Last gold date:",
            rows[-1]["gold_date"],
        )

        print(
            "Latest 22K reference:",
            rows[-1][
                "reference_22k_inr_per_gram"
            ],
            "INR per gram",
        )

    print(f"Errors: {len(errors)}")
    print(f"Warnings: {len(warnings)}")

    for error in errors:
        print(f"ERROR: {error}")

    for warning in warnings:
        print(f"WARNING: {warning}")

    print(
        "Result:",
        "PASSED" if not errors else "FAILED",
    )

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())