"""Generate an international 22K gold reference in INR per gram.

The derived reference combines:

- International gold prices from GoldPrice.org
- USD/INR exchange rates from Federal Reserve Board data via FRED

Formula:

    24K INR per gram
        = USD per troy ounce
        / 31.1034768
        * INR per USD

    22K INR per gram
        = 24K INR per gram
        * 22 / 24

By default, the script creates a preview file.

Use --apply to replace the permanent derived dataset after validation.
"""

from __future__ import annotations

import argparse
import csv
import os
import tempfile
from bisect import bisect_right
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

GOLD_INPUT_PATH = (
    PROJECT_ROOT
    / "data"
    / "international_gold_spot.csv"
)

FX_INPUT_PATH = (
    PROJECT_ROOT
    / "data"
    / "usd_inr_history.csv"
)

PREVIEW_OUTPUT_PATH = (
    PROJECT_ROOT
    / "international_gold_22k_inr_preview.csv"
)

PERMANENT_OUTPUT_PATH = (
    PROJECT_ROOT
    / "data"
    / "international_gold_22k_inr.csv"
)

TROY_OUNCE_GRAMS = Decimal("31.1034768")
PURITY_FACTOR_22K = Decimal("22") / Decimal("24")

MAX_FX_GAP_DAYS = 7

GOLD_FIELDS = [
    "timestamp_utc",
    "date",
    "price_usd_per_troy_ounce",
    "price_usd_per_gram",
    "source",
]

FX_FIELDS = [
    "date",
    "usd_inr",
    "source",
    "series",
]

OUTPUT_FIELDS = [
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


def parse_args() -> argparse.Namespace:
    """Read command-line options."""

    parser = argparse.ArgumentParser(
        description=(
            "Generate the international-derived "
            "22K gold reference in INR per gram."
        )
    )

    parser.add_argument(
        "--gold-input",
        type=Path,
        default=GOLD_INPUT_PATH,
        help=(
            "International gold CSV. Defaults to "
            "data/international_gold_spot.csv."
        ),
    )

    parser.add_argument(
        "--fx-input",
        type=Path,
        default=FX_INPUT_PATH,
        help=(
            "USD/INR history CSV. Defaults to "
            "data/usd_inr_history.csv."
        ),
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=PREVIEW_OUTPUT_PATH,
        help=(
            "Preview output path. Ignored when "
            "--apply is used."
        ),
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Write the permanent derived dataset. "
            "Without this option, only a preview "
            "file is created."
        ),
    )

    return parser.parse_args()


def parse_decimal(
    value: str,
    field_name: str,
) -> Decimal:
    """Parse a positive finite Decimal value."""

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


def read_gold_records(
    path: Path,
) -> list[dict[str, object]]:
    """Read international gold observations."""

    records: list[dict[str, object]] = []

    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        reader = csv.DictReader(file)

        if reader.fieldnames != GOLD_FIELDS:
            raise ValueError(
                "Unexpected international gold columns. "
                f"Expected {GOLD_FIELDS}; "
                f"found {reader.fieldnames}."
            )

        for row_number, row in enumerate(
            reader,
            start=2,
        ):
            try:
                timestamp = datetime.fromisoformat(
                    row["timestamp_utc"].replace(
                        "Z",
                        "+00:00",
                    )
                )

                gold_date = date.fromisoformat(
                    row["date"]
                )

                usd_per_ounce = parse_decimal(
                    row[
                        "price_usd_per_troy_ounce"
                    ],
                    "USD per troy ounce",
                )

            except Exception as exc:
                raise ValueError(
                    f"Gold row {row_number}: {exc}"
                ) from exc

            if timestamp.date() != gold_date:
                raise ValueError(
                    f"Gold row {row_number}: date does "
                    "not match the UTC timestamp."
                )

            if row["source"].strip() != "GoldPrice.org":
                raise ValueError(
                    f"Gold row {row_number}: unexpected "
                    "source attribution."
                )

            records.append({
                "timestamp": timestamp,
                "timestamp_text":
                    row["timestamp_utc"].strip(),
                "date": gold_date,
                "usd_per_ounce": usd_per_ounce,
                "source": "GoldPrice.org",
            })

    records.sort(
        key=lambda record: record["timestamp"]
    )

    if not records:
        raise ValueError(
            "No international gold records were found."
        )

    return records


def read_fx_records(
    path: Path,
) -> list[dict[str, object]]:
    """Read validated USD/INR exchange-rate history."""

    records: list[dict[str, object]] = []

    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        reader = csv.DictReader(file)

        if reader.fieldnames != FX_FIELDS:
            raise ValueError(
                "Unexpected USD/INR columns. "
                f"Expected {FX_FIELDS}; "
                f"found {reader.fieldnames}."
            )

        for row_number, row in enumerate(
            reader,
            start=2,
        ):
            try:
                fx_date = date.fromisoformat(
                    row["date"]
                )

                usd_inr = parse_decimal(
                    row["usd_inr"],
                    "USD/INR rate",
                )

            except Exception as exc:
                raise ValueError(
                    f"FX row {row_number}: {exc}"
                ) from exc

            records.append({
                "date": fx_date,
                "usd_inr": usd_inr,
                "source": row["source"].strip(),
                "series": row["series"].strip(),
            })

    records.sort(
        key=lambda record: record["date"]
    )

    if not records:
        raise ValueError(
            "No USD/INR records were found."
        )

    return records


def find_fx_record(
    gold_date: date,
    fx_dates: list[date],
    fx_records: list[dict[str, object]],
) -> dict[str, object] | None:
    """Find the latest FX record on or before a gold date."""

    position = bisect_right(
        fx_dates,
        gold_date,
    )

    if position == 0:
        return None

    return fx_records[position - 1]


def generate_rows(
    gold_records: list[dict[str, object]],
    fx_records: list[dict[str, object]],
) -> tuple[
    list[dict[str, str]],
    list[str],
]:
    """Generate reproducible 22K INR reference records."""

    fx_dates = [
        record["date"]
        for record in fx_records
    ]

    output_rows: list[dict[str, str]] = []
    warnings: list[str] = []

    for gold_record in gold_records:
        gold_date = gold_record["date"]

        fx_record = find_fx_record(
            gold_date,
            fx_dates,
            fx_records,
        )

        if fx_record is None:
            warnings.append(
                "No earlier USD/INR rate exists for "
                f"{gold_date.isoformat()}."
            )

            continue

        fx_date = fx_record["date"]
        gap_days = (
            gold_date - fx_date
        ).days

        if gap_days > MAX_FX_GAP_DAYS:
            warnings.append(
                "USD/INR gap exceeds "
                f"{MAX_FX_GAP_DAYS} days for "
                f"{gold_date.isoformat()}."
            )

            continue

        usd_per_ounce = gold_record[
            "usd_per_ounce"
        ]

        usd_inr = fx_record["usd_inr"]

        reference_24k = (
            usd_per_ounce
            / TROY_OUNCE_GRAMS
            * usd_inr
        )

        reference_22k = (
            reference_24k
            * PURITY_FACTOR_22K
        )

        output_rows.append({
            "gold_timestamp_utc":
                gold_record["timestamp_text"],
            "gold_date":
                gold_date.isoformat(),
            "fx_date":
                fx_date.isoformat(),
            "fx_gap_days":
                str(gap_days),
            "usd_per_troy_ounce":
                f"{usd_per_ounce:.4f}",
            "usd_inr":
                f"{usd_inr:.4f}",
            "reference_24k_inr_per_gram":
                f"{reference_24k:.4f}",
            "reference_22k_inr_per_gram":
                f"{reference_22k:.4f}",
            "gold_source":
                str(gold_record["source"]),
            "fx_source":
                str(fx_record["source"]),
            "fx_series":
                str(fx_record["series"]),
        })

    return output_rows, warnings


def validate_output_rows(
    rows: list[dict[str, str]],
) -> None:
    """Run key checks before writing the derived file."""

    if not rows:
        raise ValueError(
            "The derived dataset contains no records."
        )

    previous_timestamp: datetime | None = None
    seen_timestamps: set[str] = set()

    for row_number, row in enumerate(
        rows,
        start=2,
    ):
        timestamp_text = row[
            "gold_timestamp_utc"
        ]

        timestamp = datetime.fromisoformat(
            timestamp_text.replace(
                "Z",
                "+00:00",
            )
        )

        if timestamp_text in seen_timestamps:
            raise ValueError(
                f"Derived row {row_number}: "
                "duplicate gold timestamp."
            )

        seen_timestamps.add(timestamp_text)

        if (
            previous_timestamp is not None
            and timestamp <= previous_timestamp
        ):
            raise ValueError(
                f"Derived row {row_number}: "
                "timestamps are not increasing."
            )

        previous_timestamp = timestamp

        fx_gap_days = int(
            row["fx_gap_days"]
        )

        if not (
            0
            <= fx_gap_days
            <= MAX_FX_GAP_DAYS
        ):
            raise ValueError(
                f"Derived row {row_number}: "
                "invalid exchange-rate gap."
            )

        reference_22k = parse_decimal(
            row[
                "reference_22k_inr_per_gram"
            ],
            "22K INR reference",
        )

        if reference_22k <= 0:
            raise ValueError(
                f"Derived row {row_number}: "
                "invalid 22K reference."
            )


def write_output(
    path: Path,
    rows: list[dict[str, str]],
) -> None:
    """Write the derived dataset."""

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=OUTPUT_FIELDS,
        )

        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    """Create a preview or permanent derived dataset."""

    args = parse_args()

    try:
        gold_path = args.gold_input.resolve()
        fx_path = args.fx_input.resolve()

        output_path = (
            PERMANENT_OUTPUT_PATH
            if args.apply
            else args.output.resolve()
        )

        gold_records = read_gold_records(
            gold_path
        )

        fx_records = read_fx_records(
            fx_path
        )

        rows, warnings = generate_rows(
            gold_records,
            fx_records,
        )

        validate_output_rows(rows)

        if args.apply:
            output_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            file_descriptor, temporary_name = (
                tempfile.mkstemp(
                    prefix=(
                        "international_gold_22k_"
                    ),
                    suffix=".csv",
                    dir=output_path.parent,
                )
            )

            os.close(file_descriptor)

            temporary_path = Path(
                temporary_name
            )

            try:
                write_output(
                    temporary_path,
                    rows,
                )

                os.replace(
                    temporary_path,
                    output_path,
                )

            finally:
                temporary_path.unlink(
                    missing_ok=True
                )

        else:
            write_output(
                output_path,
                rows,
            )

        print(
            "International 22K INR reference"
        )
        print(
            "-------------------------------"
        )
        print(
            "Gold records read:",
            len(gold_records),
        )
        print(
            "FX records read:",
            len(fx_records),
        )
        print(
            "Derived records:",
            len(rows),
        )
        print(
            "Warnings:",
            len(warnings),
        )

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

        for warning in warnings[:20]:
            print(
                "WARNING:",
                warning,
            )

        if len(warnings) > 20:
            print(
                "Additional warnings not shown:",
                len(warnings) - 20,
            )

        print(
            "Mode:",
            "APPLIED" if args.apply
            else "PREVIEW",
        )

        print(
            "Output:",
            output_path,
        )

        print("Result: PASSED")

        return 0

    except Exception as exc:
        print(
            "Reference generation failed:",
            exc,
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(main())