"""Read-only foundation for international gold updates.

This module parses saved GoldPrice.org historical and recent
responses and compares incoming observations with immutable
permanent history.

It does not download data or modify permanent files yet.
"""

from __future__ import annotations

import csv
import json
import argparse
import subprocess
import tempfile
import os
import shutil

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from validate_international_gold import (
    validate_file,
)


TROY_OUNCE_GRAMS = Decimal("31.1034768")
EXPECTED_SOURCE = "GoldPrice.org"

EXPECTED_FIELDS = [
    "timestamp_utc",
    "date",
    "price_usd_per_troy_ounce",
    "price_usd_per_gram",
    "source",
]

HISTORICAL_INSTRUMENT = "USD-XAU!"
RECENT_INSTRUMENT = "USD-XAU"
RECENT_INTERVAL = timedelta(seconds=10)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

PERMANENT_PATH = (
    PROJECT_ROOT
    / "data"
    / "international_gold_spot.csv"
)

BACKUP_DIRECTORY = (
    PROJECT_ROOT
    / "data"
    / "backups"
)

HISTORICAL_URL = (
    "https://data-asg.goldprice.org/"
    "GetDataHistorical/USD-XAU/0"
)

RECENT_URL = (
    "https://data-asg.goldprice.org/"
    "GetData/USD-XAU/0"
)


def parse_price(value: str) -> Decimal:
    """Parse one positive, finite gold price."""

    cleaned_value = value.strip()

    try:
        price = Decimal(cleaned_value)
    except InvalidOperation as exc:
        raise ValueError(
            f"Invalid gold price: {value!r}"
        ) from exc

    if not price.is_finite():
        raise ValueError(
            f"Non-finite gold price: {value!r}"
        )

    if price <= 0:
        raise ValueError(
            f"Non-positive gold price: {value!r}"
        )

    return price


def calculate_usd_per_gram(
    usd_per_troy_ounce: Decimal,
) -> Decimal:
    """Convert a troy-ounce gold price to a gram price."""

    if not usd_per_troy_ounce.is_finite():
        raise ValueError(
            "The troy-ounce price must be finite."
        )

    if usd_per_troy_ounce <= 0:
        raise ValueError(
            "The troy-ounce price must be positive."
        )

    return (
        usd_per_troy_ounce /
        TROY_OUNCE_GRAMS
    )


def parse_historical_timestamp(
    value: str,
) -> datetime:
    """Convert a GoldPrice.org historical time code to UTC."""

    cleaned_value = value.strip()

    try:
        time_code = Decimal(cleaned_value)
    except InvalidOperation as exc:
        raise ValueError(
            f"Invalid historical time code: {value!r}"
        ) from exc

    if not time_code.is_finite():
        raise ValueError(
            "Non-finite historical time code: "
            f"{value!r}"
        )

    if time_code <= 0:
        raise ValueError(
            "Non-positive historical time code: "
            f"{value!r}"
        )

    milliseconds = (
        time_code * Decimal("100000")
    )

    if (
        milliseconds !=
        milliseconds.to_integral_value()
    ):
        raise ValueError(
            "Historical time code does not produce "
            f"whole milliseconds: {value!r}"
        )

    return datetime.fromtimestamp(
        int(milliseconds) / 1000,
        tz=timezone.utc,
    )


def normalize_utc_datetime(
    value: datetime,
) -> datetime:
    """Normalize an aware datetime to UTC milliseconds."""

    if (
        value.tzinfo is None or
        value.utcoffset() is None
    ):
        raise ValueError(
            "The timestamp must include a timezone."
        )

    utc_value = value.astimezone(
        timezone.utc
    )

    milliseconds = (
        utc_value.microsecond // 1000
    )

    return utc_value.replace(
        microsecond=milliseconds * 1000
    )


def format_timestamp(
    value: datetime,
) -> str:
    """Format a UTC datetime with millisecond precision."""

    utc_value = normalize_utc_datetime(
        value
    )

    return (
        utc_value
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def create_record(
    timestamp: datetime,
    ounce_price: Decimal,
) -> dict[str, object]:
    """Create one normalized international observation."""

    utc_timestamp = normalize_utc_datetime(
        timestamp
    )

    return {
        "timestamp": utc_timestamp,
        "timestamp_utc": format_timestamp(
            utc_timestamp
        ),
        "date": utc_timestamp.date().isoformat(),
        "usd_per_troy_ounce": ounce_price,
        "usd_per_gram": calculate_usd_per_gram(
            ounce_price
        ),
        "source": EXPECTED_SOURCE,
    }


def unpack_response(
    payload: str,
    expected_instrument: str,
) -> list[str]:
    """Read a GoldPrice.org one-string array response."""

    try:
        outer_value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "GoldPrice.org response is not valid JSON."
        ) from exc

    if (
        not isinstance(outer_value, list) or
        len(outer_value) != 1 or
        not isinstance(outer_value[0], str)
    ):
        raise ValueError(
            "GoldPrice.org response must be an array "
            "containing one string."
        )

    values = [
        value.strip()
        for value in outer_value[0].split(",")
    ]

    if (
        not values or
        values[0] != expected_instrument
    ):
        found_instrument = (
            values[0]
            if values
            else None
        )

        raise ValueError(
            "Unexpected GoldPrice.org instrument: "
            f"{found_instrument!r}; expected "
            f"{expected_instrument!r}."
        )

    return values[1:]


def parse_historical_response(
    payload: str,
) -> list[dict[str, object]]:
    """Parse historical time-code and price pairs."""

    values = unpack_response(
        payload,
        HISTORICAL_INSTRUMENT,
    )

    if (
        not values or
        len(values) % 2 != 0
    ):
        raise ValueError(
            "Historical response must contain "
            "time-code and price pairs."
        )

    records: list[dict[str, object]] = []
    seen_timestamps: dict[str, Decimal] = {}

    for index in range(
        0,
        len(values),
        2,
    ):
        timestamp = parse_historical_timestamp(
            values[index]
        )

        ounce_price = parse_price(
            values[index + 1]
        )

        record = create_record(
            timestamp,
            ounce_price,
        )

        timestamp_text = str(
            record["timestamp_utc"]
        )

        previous_price = seen_timestamps.get(
            timestamp_text
        )

        if (
            previous_price is not None and
            previous_price != ounce_price
        ):
            raise ValueError(
                "Historical response has conflicting "
                f"prices for {timestamp_text}."
            )

        if previous_price is None:
            seen_timestamps[
                timestamp_text
            ] = ounce_price

            records.append(record)

    records.sort(
        key=lambda record:
            record["timestamp"]
    )

    return records


def parse_recent_response(
    payload: str,
    retrieval_timestamp: datetime,
) -> list[dict[str, object]]:
    """Reconstruct recent observations at ten-second intervals.

    The recent GoldPrice.org response contains prices but no timestamps. The
    retrieval timestamp is preserved and each observation is reconstructed from
    that anchor using ten-second spacing.
    """

    prices = unpack_response(
        payload,
        RECENT_INSTRUMENT,
    )

    if not prices:
        raise ValueError(
            "Recent response contains no prices."
        )

    retrieval_utc = normalize_utc_datetime(
        retrieval_timestamp
    )

    # The recent response provides prices but not timestamps. Reconstruct each
    # observation from the recorded UTC retrieval time using ten-second
    # intervals.
    total_points = len(prices)

    first_timestamp = (
        retrieval_utc -
        RECENT_INTERVAL * total_points
    )

    records: list[dict[str, object]] = []

    for index, raw_price in enumerate(
        prices
    ):
        timestamp = (
            first_timestamp +
            RECENT_INTERVAL * index
        )

        records.append(
            create_record(
                timestamp,
                parse_price(raw_price),
            )
        )

    return records


def read_permanent_history(
    path: Path,
) -> dict[str, Decimal]:
    """Read permanent history by UTC timestamp."""

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
                "Unexpected permanent-history "
                "columns. "
                f"Expected {EXPECTED_FIELDS}; "
                f"found {reader.fieldnames}."
            )

        for row_number, row in enumerate(
            reader,
            start=2,
        ):
            timestamp_text = row[
                "timestamp_utc"
            ].strip()

            try:
                timestamp = datetime.fromisoformat(
                    timestamp_text.replace(
                        "Z",
                        "+00:00",
                    )
                )
            except ValueError as exc:
                raise ValueError(
                    f"Permanent row {row_number}: "
                    "invalid timestamp "
                    f"{timestamp_text!r}."
                ) from exc

            if (
                format_timestamp(timestamp) !=
                timestamp_text
            ):
                raise ValueError(
                    f"Permanent row {row_number}: "
                    "timestamp must use UTC "
                    "millisecond format."
                )

            expected_date = (
                timestamp
                .astimezone(timezone.utc)
                .date()
                .isoformat()
            )

            if (
                row["date"].strip() !=
                expected_date
            ):
                raise ValueError(
                    f"Permanent row {row_number}: "
                    "date does not match timestamp."
                )

            ounce_price = parse_price(
                row[
                    "price_usd_per_troy_ounce"
                ]
            )

            if (
                row["source"].strip() !=
                EXPECTED_SOURCE
            ):
                raise ValueError(
                    f"Permanent row {row_number}: "
                    "unexpected source attribution."
                )

            if timestamp_text in observations:
                raise ValueError(
                    "Permanent history has duplicate "
                    f"timestamp {timestamp_text}."
                )

            observations[
                timestamp_text
            ] = ounce_price

    return observations


def compare_histories(
    permanent: dict[str, Decimal],
    incoming: list[dict[str, object]],
) -> tuple[
    list[dict[str, object]],
    list[tuple[str, Decimal, Decimal]],
]:
    """Find additions and immutable-history conflicts."""

    additions: list[
        dict[str, object]
    ] = []

    # Existing timestamps are immutable. Identical incoming values are ignored,
    # while a different price at the same timestamp is a conflict.

    conflicts: list[
        tuple[str, Decimal, Decimal]
    ] = []

    incoming_by_timestamp: dict[
        str,
        dict[str, object],
    ] = {}

    for record in incoming:
        timestamp_text = str(
            record["timestamp_utc"]
        )

        incoming_price = record[
            "usd_per_troy_ounce"
        ]

        if not isinstance(
            incoming_price,
            Decimal,
        ):
            raise TypeError(
                "Incoming ounce prices must "
                "use Decimal."
            )

        previous_incoming = (
            incoming_by_timestamp.get(
                timestamp_text
            )
        )

        if previous_incoming is not None:
            previous_price = (
                previous_incoming[
                    "usd_per_troy_ounce"
                ]
            )

            if previous_price != incoming_price:
                raise ValueError(
                    "Incoming data has conflicting "
                    f"prices for {timestamp_text}."
                )

            continue

        incoming_by_timestamp[
            timestamp_text
        ] = record

        permanent_price = permanent.get(
            timestamp_text
        )

        if permanent_price is None:
            additions.append(record)
        elif permanent_price != incoming_price:
            conflicts.append(
                (
                    timestamp_text,
                    permanent_price,
                    incoming_price,
                )
            )

    additions.sort(
        key=lambda record:
            record["timestamp"]
    )

    return additions, conflicts

def parse_args() -> argparse.Namespace:
    """Read command-line preview options."""

    parser = argparse.ArgumentParser(
        description=(
            "Fetch and preview an international "
            "gold-history update."
        )
    )

    parser.add_argument(
        "--fetch",
        action="store_true",
        help=(
            "Download historical and recent "
            "GoldPrice.org responses."
        ),
    )

    parser.add_argument(
        "--recent-input",
        type=Path,
        help=(
            "Path to a recent-data JSON export "
            "downloaded from GoldPrice.org."
        ),
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Apply validated additions to permanent "
            "history. Without this option, no "
            "permanent file is changed."
        ),
    )

    return parser.parse_args()


def download_response(
    url: str,
    prefix: str,
) -> tuple[str, datetime]:
    """Download one response and record its UTC completion time."""

    temporary_file = tempfile.NamedTemporaryFile(
        prefix=prefix,
        suffix=".json",
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
        url,
    ]

    try:
        subprocess.run(
            command,
            check=True,
        )

        retrieval_timestamp = datetime.now(
            timezone.utc
        )

        if not temporary_path.exists():
            raise RuntimeError(
                "The download did not create a file."
            )

        if temporary_path.stat().st_size == 0:
            raise RuntimeError(
                "The download created an empty file."
            )

        payload = temporary_path.read_text(
            encoding="utf-8"
        )

        # Verify that the response contains valid JSON
        # before the temporary file is removed.
        try:
            json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "The downloaded response is not "
                "valid JSON."
            ) from exc

        return payload, retrieval_timestamp

    finally:
        temporary_path.unlink(
            missing_ok=True
        )

def read_recent_browser_export(
    path: Path,
) -> tuple[str, datetime]:
    """Read a recent-data export created in the browser."""

    if not path.exists():
        raise FileNotFoundError(
            f"Recent export does not exist: {path}"
        )

    try:
        export_data = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except json.JSONDecodeError as exc:
        raise ValueError(
            "The recent export is not valid JSON."
        ) from exc

    if not isinstance(export_data, dict):
        raise ValueError(
            "The recent export must contain "
            "one JSON object."
        )

    expected_endpoint = (
        "https://data-asg.goldprice.org/"
        "GetData/USD-XAU/0"
    )

    if export_data.get("endpoint") != expected_endpoint:
        raise ValueError(
            "The recent export has an "
            "unexpected endpoint."
        )

    if export_data.get("instrument") != RECENT_INSTRUMENT:
        raise ValueError(
            "The recent export has an "
            "unexpected instrument."
        )

    if export_data.get("source") != EXPECTED_SOURCE:
        raise ValueError(
            "The recent export has an "
            "unexpected source attribution."
        )

    payload = export_data.get("payload")

    if not isinstance(payload, list):
        raise ValueError(
            "The recent export payload must "
            "be a JSON array."
        )

    retrieved_at_text = export_data.get(
        "retrieved_at_utc"
    )

    if not isinstance(retrieved_at_text, str):
        raise ValueError(
            "The recent export is missing "
            "retrieved_at_utc."
        )

    try:
        retrieval_timestamp = (
            datetime.fromisoformat(
                retrieved_at_text.replace(
                    "Z",
                    "+00:00",
                )
            )
        )
    except ValueError as exc:
        raise ValueError(
            "The recent export has an invalid "
            "retrieval timestamp."
        ) from exc

    if (
        retrieval_timestamp.tzinfo is None or
        retrieval_timestamp.utcoffset() is None
    ):
        raise ValueError(
            "The retrieval timestamp must "
            "include a timezone."
        )

    expected_count = export_data.get(
        "price_count"
    )

    if (
        not isinstance(expected_count, int) or
        expected_count < 1
    ):
        raise ValueError(
            "The recent export has an invalid "
            "price_count."
        )

    if (
        len(payload) != 1 or
        not isinstance(payload[0], str)
    ):
        raise ValueError(
            "The recent export payload has an "
            "unexpected structure."
        )

    actual_count = (
        len(payload[0].split(",")) - 1
    )

    if actual_count != expected_count:
        raise ValueError(
            "The recent export price count does "
            "not match its payload."
        )

    return (
        json.dumps(payload),
        retrieval_timestamp,
    )

def combine_incoming_records(
    historical_records: list[
        dict[str, object]
    ],
    recent_records: list[
        dict[str, object]
    ],
) -> list[dict[str, object]]:
    """Combine records while rejecting timestamp conflicts."""

    combined_by_timestamp: dict[
        str,
        dict[str, object],
    ] = {}

    for record in [
        *historical_records,
        *recent_records,
    ]:
        timestamp_text = str(
            record["timestamp_utc"]
        )

        incoming_price = record[
            "usd_per_troy_ounce"
        ]

        if not isinstance(
            incoming_price,
            Decimal,
        ):
            raise TypeError(
                "Incoming prices must use Decimal."
            )

        previous_record = (
            combined_by_timestamp.get(
                timestamp_text
            )
        )

        if previous_record is not None:
            previous_price = previous_record[
                "usd_per_troy_ounce"
            ]

            if previous_price != incoming_price:
                raise ValueError(
                    "Historical and recent responses "
                    "contain conflicting prices for "
                    f"{timestamp_text}."
                )

            continue

        combined_by_timestamp[
            timestamp_text
        ] = record

    combined_records = list(
        combined_by_timestamp.values()
    )

    combined_records.sort(
        key=lambda record:
            record["timestamp"]
    )

    return combined_records


def count_unchanged_records(
    permanent: dict[str, Decimal],
    incoming: list[dict[str, object]],
) -> int:
    """Count exact incoming matches already in history."""

    unchanged = 0

    for record in incoming:
        timestamp_text = str(
            record["timestamp_utc"]
        )

        incoming_price = record[
            "usd_per_troy_ounce"
        ]

        if (
            permanent.get(timestamp_text) ==
            incoming_price
        ):
            unchanged += 1

    return unchanged


def print_preview(
    permanent_count: int,
    historical_count: int,
    recent_count: int,
    incoming_count: int,
    unchanged_count: int,
    additions: list[dict[str, object]],
    conflicts: list[
        tuple[str, Decimal, Decimal]
    ],
) -> None:
    """Print a readable, read-only update preview."""

    print("International gold update preview")
    print("---------------------------------")

    print(
        "Permanent records:",
        permanent_count,
    )

    print(
        "Historical response records:",
        historical_count,
    )

    print(
        "Recent response records:",
        recent_count,
    )

    print(
        "Combined incoming records:",
        incoming_count,
    )

    print(
        "Existing unchanged:",
        unchanged_count,
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
        print(
            "First addition:",
            additions[0]["timestamp_utc"],
            additions[0][
                "usd_per_troy_ounce"
            ],
        )

        print(
            "Last addition:",
            additions[-1]["timestamp_utc"],
            additions[-1][
                "usd_per_troy_ounce"
            ],
        )

    for (
        timestamp_text,
        permanent_price,
        incoming_price,
    ) in conflicts[:20]:
        print(
            "CONFLICT:",
            timestamp_text,
            "permanent=",
            permanent_price,
            "incoming=",
            incoming_price,
        )

    if len(conflicts) > 20:
        print(
            "Additional conflicts not shown:",
            len(conflicts) - 20,
        )


def run_fetch_preview() -> int:
    """Download and preview the proposed update."""

    print(
        "Downloading GoldPrice.org historical "
        "response..."
    )

    historical_payload, _ = download_response(
        HISTORICAL_URL,
        "goldprice_historical_",
    )

    print(
        "Downloading GoldPrice.org recent "
        "response..."
    )

    (
        recent_payload,
        recent_retrieval_timestamp,
    ) = download_response(
        RECENT_URL,
        "goldprice_recent_",
    )

    historical_records = (
        parse_historical_response(
            historical_payload
        )
    )

    recent_records = parse_recent_response(
        recent_payload,
        recent_retrieval_timestamp,
    )

    incoming_records = (
        combine_incoming_records(
            historical_records,
            recent_records,
        )
    )

    permanent = read_permanent_history(
        PERMANENT_PATH
    )

    additions, conflicts = compare_histories(
        permanent,
        incoming_records,
    )

    unchanged_count = count_unchanged_records(
        permanent,
        incoming_records,
    )

    print_preview(
        len(permanent),
        len(historical_records),
        len(recent_records),
        len(incoming_records),
        unchanged_count,
        additions,
        conflicts,
    )

    if conflicts:
        print(
            "Result: FAILED because existing "
            "historical values changed."
        )

        return 1

    print(
        "Result: PREVIEW ONLY. "
        "No permanent file was changed."
    )

    return 0

def run_recent_input_preview(
    recent_input: Path,
    apply: bool = False,
) -> int:
    """Preview or safely apply a browser export."""

    (
        recent_payload,
        retrieval_timestamp,
    ) = read_recent_browser_export(
        recent_input
    )

    recent_records = parse_recent_response(
        recent_payload,
        retrieval_timestamp,
    )

    permanent = read_permanent_history(
        PERMANENT_PATH
    )

    additions, conflicts = compare_histories(
        permanent,
        recent_records,
    )

    unchanged_count = count_unchanged_records(
        permanent,
        recent_records,
    )

    print_preview(
        len(permanent),
        0,
        len(recent_records),
        len(recent_records),
        unchanged_count,
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

    if not apply:
        print(
            "Result: PREVIEW ONLY. "
            "No permanent file was changed."
        )

        return 0

    apply_additions(
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

    print(
        "Result: PASSED"
    )

    return 0

def format_ounce_price(
    value: Decimal,
) -> str:
    """Format an ounce price with four decimals."""

    return f"{value:.4f}"


def format_gram_price(
    value: Decimal,
) -> str:
    """Format a gram price with six decimals."""

    return f"{value:.6f}"


def write_history(
    path: Path,
    observations: dict[str, Decimal],
) -> None:
    """Write chronologically sorted international history."""

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

        for timestamp_text in sorted(
            observations
        ):
            ounce_price = observations[
                timestamp_text
            ]

            timestamp = datetime.fromisoformat(
                timestamp_text.replace(
                    "Z",
                    "+00:00",
                )
            ).astimezone(timezone.utc)

            writer.writerow({
                "timestamp_utc": timestamp_text,
                "date":
                    timestamp.date().isoformat(),
                "price_usd_per_troy_ounce":
                    format_ounce_price(
                        ounce_price
                    ),
                "price_usd_per_gram":
                    format_gram_price(
                        calculate_usd_per_gram(
                            ounce_price
                        )
                    ),
                "source": EXPECTED_SOURCE,
            })


def create_backup(
    path: Path,
) -> Path:
    """Create a timestamped permanent-history backup."""

    BACKUP_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    backup_path = (
        BACKUP_DIRECTORY
        / (
            "international_gold_spot_"
            f"{timestamp}.csv"
        )
    )

    shutil.copy2(
        path,
        backup_path,
    )

    return backup_path


def require_valid_candidate(
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
                "VALIDATION ERROR:",
                error,
            )

        raise ValueError(
            "Candidate international history "
            "failed validation."
        )


def apply_additions(
    permanent: dict[str, Decimal],
    additions: list[dict[str, object]],
) -> None:
    """Apply validated additions with backup protection."""

    merged = dict(permanent)

    for record in additions:
        timestamp_text = str(
            record["timestamp_utc"]
        )

        ounce_price = record[
            "usd_per_troy_ounce"
        ]

        if not isinstance(
            ounce_price,
            Decimal,
        ):
            raise TypeError(
                "Addition prices must use Decimal."
            )

        existing_price = merged.get(
            timestamp_text
        )

        if (
            existing_price is not None and
            existing_price != ounce_price
        ):
            raise ValueError(
                "Apply detected a historical "
                f"conflict at {timestamp_text}."
            )

        merged[
            timestamp_text
        ] = ounce_price

    PERMANENT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    file_descriptor, temporary_name = (
        tempfile.mkstemp(
            prefix=
                "international_gold_spot_",
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

        require_valid_candidate(
            temporary_path
        )

        if PERMANENT_PATH.exists():
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

        final_errors, _, final_rows = (
            validate_file(
                PERMANENT_PATH
            )
        )

        if final_errors:
            if (
                backup_path is not None and
                backup_path.exists()
            ):
                shutil.copy2(
                    backup_path,
                    PERMANENT_PATH,
                )

            raise ValueError(
                "Final validation failed. "
                "The previous permanent file "
                "was restored."
            )

        print(
            "Final records:",
            len(final_rows),
        )

    finally:
        temporary_path.unlink(
            missing_ok=True
        )

def main() -> int:
    """Run the requested read-only preview."""

    args = parse_args()

    if args.apply and args.recent_input is None:
        print(
            "--apply currently requires "
            "--recent-input."
        )

        return 1

    if (
        args.fetch and
        args.recent_input is not None
    ):
        print(
            "Choose either --fetch or "
            "--recent-input, not both."
        )

        return 1

    try:
        if args.recent_input is not None:
            return run_recent_input_preview(
                args.recent_input.resolve(),
                apply=args.apply,
            )

        if args.fetch:
            return run_fetch_preview()

        print(
            "No input mode selected. Use "
            "--recent-input for a browser export."
        )

        return 1

    except Exception as exc:
        print(
            "International gold update failed:",
            exc,
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(main())