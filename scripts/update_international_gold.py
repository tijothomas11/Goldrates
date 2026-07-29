"""Read-only foundation for international gold updates.

This module parses saved GoldPrice.org historical and recent
responses and compares incoming observations with immutable
permanent history.

It does not download data or modify permanent files yet.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


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
    """Reconstruct ten-second recent observations."""

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