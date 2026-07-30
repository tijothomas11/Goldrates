"""Safely update permanent USD/INR exchange-rate history.

Examples:

Preview a manually downloaded FRED file:

    python scripts/update_usd_inr.py \
        --input DEXINUS.csv

Apply a manually downloaded FRED file:

    python scripts/update_usd_inr.py \
        --input DEXINUS.csv \
        --apply

Preview data downloaded automatically from FRED:

    python scripts/update_usd_inr.py \
        --fetch

Apply data downloaded automatically from FRED:

    python scripts/update_usd_inr.py \
        --fetch \
        --apply

Without --apply, this script is read-only.
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import tempfile
import subprocess
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from validate_usd_inr import (
    EXPECTED_FIELDS,
    EXPECTED_SERIES,
    EXPECTED_SOURCE,
    validate_file,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_RAW_PATH = (
    PROJECT_ROOT
    / "DEXINUS.csv"
)

PERMANENT_PATH = (
    PROJECT_ROOT
    / "data"
    / "usd_inr_history.csv"
)

BACKUP_DIRECTORY = (
    PROJECT_ROOT
    / "data"
    / "backups"
)

FRED_CSV_URL = (
    "https://fred.stlouisfed.org/"
    "graph/fredgraph.csv?id=DEXINUS"
)

RAW_FIELDS = [
    "observation_date",
    "DEXINUS",
]


def parse_args() -> argparse.Namespace:
    """Read command-line options."""

    parser = argparse.ArgumentParser(
        description=(
            "Preview or safely apply a USD/INR "
            "history update."
        )
    )

    source_group = parser.add_mutually_exclusive_group()

    source_group.add_argument(
        "--input",
        type=Path,
        help=(
            "Path to a manually downloaded "
            "DEXINUS CSV file."
        ),
    )

    source_group.add_argument(
        "--fetch",
        action="store_true",
        help=(
            "Download the latest DEXINUS CSV "
            "from FRED."
        ),
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Apply the validated update. Without "
            "this option, no permanent file changes."
        ),
    )

    return parser.parse_args()


def parse_rate(value: str) -> Decimal | None:
    """Parse one raw FRED rate.

    Missing FRED values are represented by an empty
    string or a period and are intentionally ignored.
    """

    cleaned_value = value.strip()

    if cleaned_value in {"", "."}:
        return None

    try:
        rate = Decimal(cleaned_value)

    except InvalidOperation as exc:
        raise ValueError(
            f"Invalid DEXINUS value: {value!r}"
        ) from exc

    if not rate.is_finite():
        raise ValueError(
            f"Non-finite DEXINUS value: {value!r}"
        )

    if rate <= 0:
        raise ValueError(
            f"Non-positive DEXINUS value: {value!r}"
        )

    return rate


def read_raw_fred_file(
    path: Path,
) -> dict[str, Decimal]:
    """Read valid observations from a raw FRED CSV."""

    observations: dict[str, Decimal] = {}

    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        reader = csv.DictReader(file)

        if reader.fieldnames != RAW_FIELDS:
            raise ValueError(
                "Unexpected raw DEXINUS columns. "
                f"Expected {RAW_FIELDS}; "
                f"found {reader.fieldnames}."
            )

        for row_number, row in enumerate(
            reader,
            start=2,
        ):
            date_value = row[
                "observation_date"
            ].strip()

            if not date_value:
                continue

            try:
                datetime.strptime(
                    date_value,
                    "%Y-%m-%d",
                )

            except ValueError as exc:
                raise ValueError(
                    f"Row {row_number}: invalid date "
                    f"{date_value!r}."
                ) from exc

            rate = parse_rate(
                row["DEXINUS"]
            )

            if rate is None:
                continue

            existing_rate = observations.get(
                date_value
            )

            if (
                existing_rate is not None
                and existing_rate != rate
            ):
                raise ValueError(
                    f"Raw input contains conflicting "
                    f"values for {date_value}: "
                    f"{existing_rate} and {rate}."
                )

            observations[date_value] = rate

    if not observations:
        raise ValueError(
            "The raw DEXINUS file contains no "
            "valid observations."
        )

    return observations


def read_permanent_history(
    path: Path,
) -> dict[str, Decimal]:
    """Read the existing permanent exchange-rate history."""

    observations: dict[str, Decimal] = {}

    if not path.exists():
        return observations

    with path.open(
        "r",
        encoding="utf-8",
        newline="",
    ) as file:
        reader = csv.DictReader(file)

        if reader.fieldnames != EXPECTED_FIELDS:
            raise ValueError(
                "Unexpected permanent-history columns. "
                f"Expected {EXPECTED_FIELDS}; "
                f"found {reader.fieldnames}."
            )

        for row_number, row in enumerate(
            reader,
            start=2,
        ):
            date_value = row["date"].strip()

            try:
                rate = Decimal(
                    row["usd_inr"].strip()
                )

            except InvalidOperation as exc:
                raise ValueError(
                    f"Permanent row {row_number}: "
                    "invalid USD/INR rate."
                ) from exc

            observations[date_value] = rate

    return observations


def compare_histories(
    permanent: dict[str, Decimal],
    incoming: dict[str, Decimal],
) -> tuple[
    dict[str, Decimal],
    list[tuple[str, Decimal, Decimal]],
]:
    """Find additions and immutable-history conflicts."""

    additions: dict[str, Decimal] = {}

    conflicts: list[
        tuple[str, Decimal, Decimal]
    ] = []

    for date_value, incoming_rate in incoming.items():
        permanent_rate = permanent.get(
            date_value
        )

        if permanent_rate is None:
            additions[date_value] = incoming_rate
            continue

        if permanent_rate != incoming_rate:
            conflicts.append(
                (
                    date_value,
                    permanent_rate,
                    incoming_rate,
                )
            )

    return additions, conflicts


def format_rate(rate: Decimal) -> str:
    """Format a permanent rate with four decimals."""

    return f"{rate:.4f}"


def write_history(
    path: Path,
    observations: dict[str, Decimal],
) -> None:
    """Write sorted permanent history."""

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
            fieldnames=EXPECTED_FIELDS,
            lineterminator="\n",
        )

        writer.writeheader()

        for date_value in sorted(observations):
            writer.writerow({
                "date": date_value,
                "usd_inr": format_rate(
                    observations[date_value]
                ),
                "source": EXPECTED_SOURCE,
                "series": EXPECTED_SERIES,
            })


def download_fred_file() -> Path:
    """Download the latest raw DEXINUS CSV using curl.

    Curl is used because it handles the Codespaces and
    GitHub Actions network environment more reliably than
    Python's urllib implementation.
    """

    temporary_file = tempfile.NamedTemporaryFile(
        prefix="dexinus_",
        suffix=".csv",
        delete=False,
    )

    temporary_path = Path(
        temporary_file.name
    )

    temporary_file.close()

    command = [
        "curl",
        "--fail",
        "--location",
        "--silent",
        "--show-error",
        "--connect-timeout",
        "30",
        "--max-time",
        "180",
        "--retry",
        "3",
        "--retry-delay",
        "5",
        "--output",
        str(temporary_path),
        FRED_CSV_URL,
    ]

    try:
        print(
            "Downloading latest DEXINUS data "
            "from FRED..."
        )

        subprocess.run(
            command,
            check=True,
        )

        if not temporary_path.exists():
            raise RuntimeError(
                "FRED download did not create a file."
            )

        if temporary_path.stat().st_size == 0:
            raise RuntimeError(
                "FRED download created an empty file."
            )

        # Validate the downloaded structure immediately.
        # The regular updater parser will perform the complete
        # observation validation afterward.
        with temporary_path.open(
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as file:
            reader = csv.reader(file)
            header = next(reader, None)

        if header != RAW_FIELDS:
            raise ValueError(
                "Downloaded FRED file has unexpected "
                f"columns: {header}"
            )

    except Exception:
        temporary_path.unlink(
            missing_ok=True
        )
        raise

    print(
        "Downloaded latest DEXINUS data "
        "from FRED."
    )

    print(
        "Downloaded bytes:",
        temporary_path.stat().st_size,
    )

    return temporary_path


def create_backup(path: Path) -> Path:
    """Create a timestamped permanent-history backup."""

    BACKUP_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")

    backup_path = (
        BACKUP_DIRECTORY
        / f"usd_inr_history_{timestamp}.csv"
    )

    shutil.copy2(
        path,
        backup_path,
    )

    return backup_path


def validate_candidate(
    candidate_path: Path,
) -> None:
    """Require a candidate file to pass validation."""

    errors, warnings, rows = validate_file(
        candidate_path
    )

    print(
        "Candidate records:",
        len(rows),
    )

    print(
        "Candidate warnings:",
        len(warnings),
    )

    if errors:
        for error in errors:
            print(
                f"VALIDATION ERROR: {error}"
            )

        raise ValueError(
            "Candidate USD/INR history "
            "failed validation."
        )


def apply_update(
    permanent: dict[str, Decimal],
    additions: dict[str, Decimal],
) -> None:
    """Atomically apply a validated update."""

    merged = dict(permanent)
    merged.update(additions)

    PERMANENT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    file_descriptor, temporary_name = (
        tempfile.mkstemp(
            prefix="usd_inr_history_",
            suffix=".csv",
            dir=PERMANENT_PATH.parent,
        )
    )

    os.close(file_descriptor)

    temporary_path = Path(
        temporary_name
    )

    backup_path: Path | None = None

    try:
        write_history(
            temporary_path,
            merged,
        )

        validate_candidate(
            temporary_path
        )

        if PERMANENT_PATH.exists():
            # The temporary candidate is validated before the permanent file is
            # replaced. A backup is kept so the previous history can be restored
            # if the final validation rejects the update.
            backup_path = create_backup(
                PERMANENT_PATH
            )

            print(
                "Backup created:",
                backup_path,
            )

        os.replace(
            temporary_path,
            PERMANENT_PATH,
        )

        final_errors, _, _ = validate_file(
            PERMANENT_PATH
        )

        if final_errors:
            if (
                backup_path is not None
                and backup_path.exists()
            ):
                shutil.copy2(
                    backup_path,
                    PERMANENT_PATH,
                )

            raise ValueError(
                "Final validation failed. "
                "The previous file was restored."
            )

    finally:
        temporary_path.unlink(
            missing_ok=True
        )


def print_preview(
    permanent_count: int,
    incoming_count: int,
    additions: dict[str, Decimal],
    conflicts: list[
        tuple[str, Decimal, Decimal]
    ],
) -> None:
    """Print a readable update preview."""

    print("USD/INR update preview")
    print("----------------------")
    print(
        "Permanent records:",
        permanent_count,
    )
    print(
        "Incoming valid records:",
        incoming_count,
    )
    print(
        "New observations:",
        len(additions),
    )
    print(
        "Conflicts:",
        len(conflicts),
    )

    if additions:
        sorted_dates = sorted(additions)

        print(
            "First addition:",
            sorted_dates[0],
            format_rate(
                additions[sorted_dates[0]]
            ),
        )

        print(
            "Last addition:",
            sorted_dates[-1],
            format_rate(
                additions[sorted_dates[-1]]
            ),
        )

    for (
        date_value,
        permanent_rate,
        incoming_rate,
    ) in conflicts[:20]:
        print(
            "CONFLICT:",
            date_value,
            "permanent=",
            format_rate(permanent_rate),
            "incoming=",
            format_rate(incoming_rate),
        )

    if len(conflicts) > 20:
        print(
            "Additional conflicts not shown:",
            len(conflicts) - 20,
        )


def main() -> int:
    """Preview or apply the USD/INR update."""

    args = parse_args()

    downloaded_path: Path | None = None

    try:
        if args.fetch:
            downloaded_path = (
                download_fred_file()
            )

            input_path = downloaded_path

        elif args.input is not None:
            input_path = (
                args.input.resolve()
            )

        else:
            input_path = DEFAULT_RAW_PATH

        if not input_path.exists():
            raise FileNotFoundError(
                f"Input file does not exist: "
                f"{input_path}"
            )

        permanent = read_permanent_history(
            PERMANENT_PATH
        )

        incoming = read_raw_fred_file(
            input_path
        )

        additions, conflicts = (
            compare_histories(
                permanent,
                incoming,
            )
        )

        print_preview(
            len(permanent),
            len(incoming),
            additions,
            conflicts,
        )

        if conflicts:
            print(
                "Result: FAILED because existing "
                "historical values changed."
            )

            return 1

        if not additions:
            print(
                "Result: PASSED. Permanent history "
                "is already up to date."
            )

            return 0

        if not args.apply:
            print(
                "Result: PREVIEW ONLY. "
                "No permanent file was changed."
            )

            print(
                "Run again with --apply to save "
                "the validated additions."
            )

            return 0

        apply_update(
            permanent,
            additions,
        )

        print(
            "Applied new observations:",
            len(additions),
        )

        print(
            "Permanent file:",
            PERMANENT_PATH,
        )

        print("Result: PASSED")

        return 0

    except Exception as exc:
        print(
            "USD/INR update failed:",
            exc,
        )

        return 1

    finally:
        if downloaded_path is not None:
            downloaded_path.unlink(
                missing_ok=True
            )


if __name__ == "__main__":
    raise SystemExit(main())