"""Validate the permanent USD/INR exchange-rate history.

The expected CSV schema is:

date,usd_inr,source,series

This validator is intentionally read-only. It never modifies the
input file.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_INPUT_PATH = (
    PROJECT_ROOT
    / "data"
    / "usd_inr_history.csv"
)

EXPECTED_FIELDS = [
    "date",
    "usd_inr",
    "source",
    "series",
]

EXPECTED_SOURCE = (
    "Federal Reserve Board via FRED"
)

EXPECTED_SERIES = "DEXINUS"

# These are deliberately broad sanity limits.
# They are intended to catch malformed values, not predict markets.
MIN_REASONABLE_RATE = Decimal("1")
MAX_REASONABLE_RATE = Decimal("1000")


def parse_args() -> argparse.Namespace:
    """Read command-line options."""

    parser = argparse.ArgumentParser(
        description=(
            "Validate the permanent USD/INR "
            "exchange-rate history."
        )
    )

    parser.add_argument(
        "path",
        nargs="?",
        default=str(DEFAULT_INPUT_PATH),
        help=(
            "CSV file to validate. Defaults to "
            "data/usd_inr_history.csv."
        ),
    )

    return parser.parse_args()


def parse_date(value: str) -> date:
    """Convert a YYYY-MM-DD value into a date."""

    cleaned_value = value.strip()

    if not cleaned_value:
        raise ValueError(
            "Date is empty."
        )

    return date.fromisoformat(
        cleaned_value
    )


def parse_rate(value: str) -> Decimal:
    """Convert the exchange rate into a positive Decimal."""

    cleaned_value = value.strip()

    if not cleaned_value:
        raise ValueError(
            "USD/INR rate is empty."
        )

    if cleaned_value == ".":
        raise ValueError(
            "USD/INR rate is missing."
        )

    try:
        rate = Decimal(cleaned_value)

    except InvalidOperation as exc:
        raise ValueError(
            "USD/INR rate is not a valid number."
        ) from exc

    if not rate.is_finite():
        raise ValueError(
            "USD/INR rate must be finite."
        )

    if rate <= 0:
        raise ValueError(
            "USD/INR rate must be greater than zero."
        )

    return rate


def read_rows(
    path: Path,
) -> tuple[list[dict[str, str]], list[str]]:
    """Read the CSV and validate its column names."""

    errors: list[str] = []

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

                return [], errors

            rows = list(reader)

    except OSError as exc:
        errors.append(
            f"Could not read file: {exc}"
        )

        return [], errors

    return rows, errors


def validate_file(
    path: Path,
) -> tuple[
    list[str],
    list[str],
    list[dict[str, str]],
]:
    """Validate one USD/INR history file."""

    errors: list[str] = []
    warnings: list[str] = []

    if not path.exists():
        errors.append(
            f"File does not exist: {path}"
        )

        return errors, warnings, []

    rows, read_errors = read_rows(path)
    errors.extend(read_errors)

    if read_errors:
        return errors, warnings, rows

    if not rows:
        errors.append(
            "The CSV contains no data records."
        )

        return errors, warnings, rows

    # Each date should appear once and the file should be strictly increasing
    # so the permanent history stays chronological and unambiguous.
    seen_dates: set[date] = set()
    previous_date: date | None = None

    valid_dates: list[date] = []
    valid_rates: list[Decimal] = []

    for row_number, row in enumerate(
        rows,
        start=2,
    ):
        try:
            observation_date = parse_date(
                row["date"]
            )

        except (ValueError, TypeError) as exc:
            errors.append(
                f"Row {row_number}: "
                f"invalid date: {exc}"
            )

            continue

        try:
            rate = parse_rate(
                row["usd_inr"]
            )

        except (ValueError, TypeError) as exc:
            errors.append(
                f"Row {row_number}: {exc}"
            )

            continue

        if observation_date in seen_dates:
            errors.append(
                f"Row {row_number}: duplicate date "
                f"{observation_date.isoformat()}."
            )

        seen_dates.add(
            observation_date
        )

        if (
            previous_date is not None
            and observation_date <= previous_date
        ):
            errors.append(
                f"Row {row_number}: dates are not "
                "strictly increasing."
            )

        previous_date = observation_date

        source = row["source"].strip()

        if source != EXPECTED_SOURCE:
            errors.append(
                f"Row {row_number}: unexpected source "
                f"{source!r}."
            )

        series = row["series"].strip()

        if series != EXPECTED_SERIES:
            errors.append(
                f"Row {row_number}: unexpected series "
                f"{series!r}."
            )

        if (
            rate < MIN_REASONABLE_RATE
            or rate > MAX_REASONABLE_RATE
        ):
            warnings.append(
                f"Row {row_number}: rate {rate} is "
                "outside the broad sanity range."
            )

        valid_dates.append(
            observation_date
        )

        valid_rates.append(rate)

    if valid_dates:
        if valid_dates[0] != date(
            1973,
            1,
            2,
        ):
            warnings.append(
                "The first valid DEXINUS date is "
                f"{valid_dates[0].isoformat()}, "
                "not 1973-01-02."
            )

    return errors, warnings, rows


def print_report(
    path: Path,
    errors: list[str],
    warnings: list[str],
    rows: list[dict[str, str]],
) -> None:
    """Print a beginner-friendly validation report."""

    print("USD/INR history validation")
    print("--------------------------")
    print(f"File: {path}")
    print(f"Records: {len(rows)}")

    if rows:
        print(
            f"First date: {rows[0]['date']}"
        )

        print(
            f"Last date: {rows[-1]['date']}"
        )

        print(
            f"Latest rate: "
            f"{rows[-1]['usd_inr']} INR per USD"
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


def main() -> int:
    """Run the validator."""

    args = parse_args()

    path = Path(args.path).resolve()

    errors, warnings, rows = validate_file(
        path
    )

    print_report(
        path,
        errors,
        warnings,
        rows,
    )

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())