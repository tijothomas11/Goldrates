"""
Resumable historical backfill for KeralaGold 22K gold rates.

The script collects:

- Published price for 1 gram of 22K gold, when available
- Published price for 1 pavan / 8 grams of 22K gold
- Exact per-gram price calculated from the pavan price
- Difference between the two published series
- A normalized per-gram price for later analysis
- Source URLs

The permanent history file is never modified by this script.

Output:
    data/gold_rates_full_backfill.csv

Progress:
    data/gold_rates_backfill_progress.csv

Errors:
    data/gold_rates_backfill_errors.csv
"""

from __future__ import annotations

import csv
import datetime as dt
from decimal import Decimal
from pathlib import Path
import re
import time
import urllib.error

from goldrate_tracker import fetch_html, parse_history_table


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"

ARCHIVE_LINKS_PATH = PROJECT_ROOT / "archive_links.csv"
OUTPUT_PATH = DATA_DIR / "gold_rates_full_backfill.csv"
PROGRESS_PATH = DATA_DIR / "gold_rates_backfill_progress.csv"
ERROR_LOG_PATH = DATA_DIR / "gold_rates_backfill_errors.csv"

# Delay after each webpage request.
REQUEST_DELAY_SECONDS = 0.75

# Retry temporary failures up to three times.
MAX_REQUEST_ATTEMPTS = 3

# Delay before retrying a failed request.
RETRY_DELAY_SECONDS = 3.0

BASE_URL = "https://www.keralagold.com/"

PAVAN_ARCHIVE_PATTERN = re.compile(
    r"^https://www\.keralagold\.com/"
    r"daily-gold-prices-"
    r"(january|february|march|april|may|june|"
    r"july|august|september|october|november|december)-"
    r"(\d{4})\.htm$",
    re.IGNORECASE,
)

MONTH_NUMBERS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

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

OUTPUT_FIELDS = [
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

PROGRESS_FIELDS = [
    "month",
    "status",
    "gram_records",
    "pavan_records",
    "combined_records",
    "completed_at",
]

ERROR_FIELDS = [
    "month",
    "source_type",
    "url",
    "error",
    "recorded_at",
]


def normalize_session(session: str | None) -> str:
    """
    Normalize session labels for matching and storage.

    Today and Yesterday are page-relative labels rather than
    actual market sessions, so they become a blank session.
    """
    if session is None:
        return ""

    value = session.strip()

    if not value:
        return ""

    if value.lower() in {"today", "yesterday"}:
        return ""

    return value.title()


def record_key(record) -> tuple[str, str]:
    """Return a stable date-and-session key."""

    return (
        record.date.isoformat(),
        normalize_session(record.session),
    )


def sort_key(row: dict[str, str]) -> tuple[object, ...]:
    """Sort rows by date and chronological session order."""

    session = row.get("session", "")

    return (
        row["date"],
        SESSION_ORDER.get(session, 50),
        session,
    )


def decimal_text(value: Decimal | None) -> str:
    """
    Convert a Decimal to stable CSV text.

    Whole numbers are written without a decimal point.
    Decimal fractions are preserved without trailing zeros.
    """
    if value is None:
        return ""

    normalized = value.normalize()

    if normalized == normalized.to_integral():
        return str(normalized.quantize(Decimal("1")))

    return format(normalized, "f")


def load_pavan_archive_urls(
    csv_path: Path,
) -> list[str]:
    """Load only monthly pavan archive URLs."""

    if not csv_path.exists():
        raise FileNotFoundError(
            f"Archive-link file was not found: {csv_path}"
        )

    urls: list[str] = []

    with csv_path.open(
        newline="",
        encoding="utf-8",
    ) as file:
        reader = csv.DictReader(file)

        for row in reader:
            url = row.get("url", "").strip()

            if PAVAN_ARCHIVE_PATTERN.fullmatch(url):
                urls.append(url)

    return list(dict.fromkeys(urls))


def parse_archive_month(
    pavan_url: str,
) -> tuple[int, int]:
    """Extract year and month from a pavan archive URL."""

    match = PAVAN_ARCHIVE_PATTERN.fullmatch(
        pavan_url
    )

    if match is None:
        raise ValueError(
            f"Invalid pavan archive URL: {pavan_url}"
        )

    month_name = match.group(1).lower()
    year = int(match.group(2))
    month = MONTH_NUMBERS[month_name]

    return year, month


def month_identifier(
    year: int,
    month: int,
) -> str:
    """Return a sortable YYYY-MM month identifier."""

    return f"{year:04d}-{month:02d}"


def make_gram_archive_url(
    pavan_url: str,
) -> str:
    """Derive the corresponding per-gram archive URL."""

    return pavan_url.replace(
        "daily-gold-prices-",
        "kerala-gold-rate-per-gram-",
    )


def fetch_with_retries(
    url: str,
    *,
    missing_is_allowed: bool,
):
    """
    Fetch and parse one page with retry handling.

    Returns None when the server returns 404 and the page is
    optional. Temporary failures are retried.
    """
    for attempt in range(
        1,
        MAX_REQUEST_ATTEMPTS + 1,
    ):
        try:
            html = fetch_html(url)
            records = parse_history_table(html)

            time.sleep(REQUEST_DELAY_SECONDS)

            return records

        except urllib.error.HTTPError as exc:
            if exc.code == 404 and missing_is_allowed:
                time.sleep(REQUEST_DELAY_SECONDS)
                return None

            if exc.code in {
                429,
                500,
                502,
                503,
                504,
            }:
                if attempt < MAX_REQUEST_ATTEMPTS:
                    print(
                        f"    HTTP {exc.code}; retrying "
                        f"({attempt}/{MAX_REQUEST_ATTEMPTS})..."
                    )
                    time.sleep(RETRY_DELAY_SECONDS)
                    continue

            raise

        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
        ) as exc:
            if attempt < MAX_REQUEST_ATTEMPTS:
                print(
                    f"    Temporary request failure; retrying "
                    f"({attempt}/{MAX_REQUEST_ATTEMPTS}): "
                    f"{exc}"
                )
                time.sleep(RETRY_DELAY_SECONDS)
                continue

            raise

    raise RuntimeError(
        f"Request failed after retries: {url}"
    )


def validate_month_records(
    records,
    expected_year: int,
    expected_month: int,
    source_type: str,
) -> list:
    """
    Keep only records belonging to the expected month.

    A page with parsed rows from another month is rejected.
    """
    valid_records = []

    for record in records or []:
        if (
            record.date.year == expected_year
            and record.date.month == expected_month
        ):
            valid_records.append(record)
        else:
            print(
                "    Warning: excluded out-of-month "
                f"{source_type} record: "
                f"{record.date.isoformat()}"
            )

    return valid_records


def combine_records(
    gram_records,
    pavan_records,
    gram_url: str,
    pavan_url: str,
) -> list[dict[str, str]]:
    """Combine gram and pavan records by date and session."""

    gram_by_key = {
        record_key(record): record
        for record in gram_records
    }

    pavan_by_key = {
        record_key(record): record
        for record in pavan_records
    }
        # Reconcile source rows whose dates and prices agree but
    # whose session labels differ.
    #
    # KeralaGold occasionally labels a rate as Morning or
    # Evening on the gram page while leaving the corresponding
    # pavan session blank. Only reconcile when the match is
    # unique and the prices agree exactly.
    gram_only_keys = (
        set(gram_by_key) - set(pavan_by_key)
    )

    pavan_only_keys = (
        set(pavan_by_key) - set(gram_by_key)
    )

    gram_only_by_date: dict[
        str,
        list[tuple[str, str]],
    ] = {}

    pavan_only_by_date: dict[
        str,
        list[tuple[str, str]],
    ] = {}

    for key in gram_only_keys:
        gram_only_by_date.setdefault(
            key[0],
            [],
        ).append(key)

    for key in pavan_only_keys:
        pavan_only_by_date.setdefault(
            key[0],
            [],
        ).append(key)

    shared_dates = (
        set(gram_only_by_date)
        & set(pavan_only_by_date)
    )

    for date_text in shared_dates:
        unmatched_gram_keys = (
            gram_only_by_date[date_text]
        )

        unmatched_pavan_keys = (
            pavan_only_by_date[date_text]
        )

        # Require exactly one unmatched record from each
        # source. This avoids guessing on dates with several
        # ambiguous intraday observations.
        if (
            len(unmatched_gram_keys) != 1
            or len(unmatched_pavan_keys) != 1
        ):
            continue

        gram_key = unmatched_gram_keys[0]
        pavan_key = unmatched_pavan_keys[0]

        gram_record = gram_by_key[gram_key]
        pavan_record = pavan_by_key[pavan_key]

        gram_price = Decimal(
            gram_record.price
        )

        gram_from_pavan = (
            Decimal(pavan_record.price)
            / Decimal("8")
        )

        if gram_price != gram_from_pavan:
            continue

        gram_session = gram_key[1]
        pavan_session = pavan_key[1]

        # Require one side to have a blank session. Do not
        # combine conflicting specific sessions such as
        # Morning and Evening.
        if (
            gram_session
            and pavan_session
        ):
            continue

        # Preserve whichever source provides the more specific
        # market session.
        merged_session = (
            gram_session
            or pavan_session
        )

        merged_key = (
            date_text,
            merged_session,
        )

        print(
            "    Reconciled session mismatch: "
            f"{date_text} | "
            f"gram={gram_session or 'Standard'} | "
            f"pavan={pavan_session or 'Standard'} | "
            f"merged={merged_session or 'Standard'} | "
            f"price={gram_price}"
        )

        # Remove the unmatched source keys and store both
        # records under the reconciled key.
        del gram_by_key[gram_key]
        del pavan_by_key[pavan_key]

        gram_by_key[merged_key] = gram_record
        pavan_by_key[merged_key] = pavan_record

    all_keys = sorted(
        set(gram_by_key) | set(pavan_by_key)
    )

    combined: list[dict[str, str]] = []

    for date_text, session in all_keys:
        key = (date_text, session)

        gram_record = gram_by_key.get(key)
        pavan_record = pavan_by_key.get(key)

        published_gram = (
            Decimal(gram_record.price)
            if gram_record is not None
            else None
        )

        published_pavan = (
            Decimal(pavan_record.price)
            if pavan_record is not None
            else None
        )

        gram_from_pavan = (
            published_pavan / Decimal("8")
            if published_pavan is not None
            else None
        )
                # Reject an implausible gram parse when the independently
        # published pavan value provides a credible comparison.
        if (
            published_gram is not None
            and gram_from_pavan is not None
            and abs(
                published_gram - gram_from_pavan
            ) > Decimal("100")
        ):
            print(
                f"    Warning: rejecting suspicious gram "
                f"value on {date_text} "
                f"{session or 'Standard'}: "
                f"gram={published_gram}, "
                f"pavan/8={gram_from_pavan}"
            )

            published_gram = None

        difference = (
            published_gram - gram_from_pavan
            if published_gram is not None
            and gram_from_pavan is not None
            else None
        )

        normalized_gram = (
            published_gram
            if published_gram is not None
            else gram_from_pavan
        )

        if normalized_gram is None:
            continue

        combined.append(
            {
                "date": date_text,
                "session": session,
                "price_22k_1g_published": (
                    decimal_text(published_gram)
                ),
                "price_22k_8g_published": (
                    decimal_text(published_pavan)
                ),
                "price_22k_1g_from_pavan": (
                    decimal_text(gram_from_pavan)
                ),
                "difference_22k": (
                    decimal_text(difference)
                ),
                "normalized_price_22k_1g": (
                    decimal_text(normalized_gram)
                ),
                "gram_source_url": (
                    gram_url
                    if gram_record is not None
                    else ""
                ),
                "pavan_source_url": (
                    pavan_url
                    if pavan_record is not None
                    else ""
                ),
            }
        )

    return sorted(combined, key=sort_key)


def load_existing_output(
    path: Path,
) -> dict[tuple[str, str], dict[str, str]]:
    """Load records already saved by an earlier run."""

    records: dict[
        tuple[str, str],
        dict[str, str],
    ] = {}

    if not path.exists():
        return records

    with path.open(
        newline="",
        encoding="utf-8",
    ) as file:
        reader = csv.DictReader(file)

        if reader.fieldnames != OUTPUT_FIELDS:
            raise ValueError(
                "Existing backfill output has an "
                "unexpected CSV structure. Move or delete "
                f"{path} before starting a new backfill."
            )

        for row in reader:
            date_text = row.get("date", "").strip()
            session = row.get("session", "").strip()

            if not date_text:
                continue

            records[(date_text, session)] = row

    return records


def load_completed_months(
    path: Path,
) -> set[str]:
    """Load month identifiers already completed."""

    completed= set()

    if not path.exists():
        return completed

    with path.open(
        newline="",
        encoding="utf-8",
    ) as file:
        reader = csv.DictReader(file)

        for row in reader:
            if row.get("status") == "completed":
                month = row.get("month", "").strip()

                if month:
                    completed.add(month)

    return completed


def write_csv_atomically(
    path: Path,
    fieldnames: list[str],
    rows: list[dict[str, object]],
) -> None:
    """Write a complete temporary file, then replace output."""

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = path.with_suffix(
        path.suffix + ".tmp"
    )

    with temporary_path.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )

        writer.writeheader()
        writer.writerows(rows)

    temporary_path.replace(path)


def save_output(
    output_records: dict[
        tuple[str, str],
        dict[str, str],
    ],
) -> None:
    """Atomically save all collected history records."""

    rows = sorted(
        output_records.values(),
        key=sort_key,
    )

    write_csv_atomically(
        OUTPUT_PATH,
        OUTPUT_FIELDS,
        rows,
    )


def append_progress(
    month: str,
    gram_count: int,
    pavan_count: int,
    combined_count: int,
) -> None:
    """Append one successfully completed month."""

    PROGRESS_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    file_exists = PROGRESS_PATH.exists()

    with PROGRESS_PATH.open(
        "a",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=PROGRESS_FIELDS,
        )

        if not file_exists:
            writer.writeheader()

        writer.writerow(
            {
                "month": month,
                "status": "completed",
                "gram_records": gram_count,
                "pavan_records": pavan_count,
                "combined_records": combined_count,
                "completed_at": (
                    dt.datetime.now(
                        dt.timezone.utc
                    ).isoformat()
                ),
            }
        )


def log_error(
    month: str,
    source_type: str,
    url: str,
    error: Exception | str,
) -> None:
    """Append a failure to the error CSV."""

    ERROR_LOG_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    file_exists = ERROR_LOG_PATH.exists()

    with ERROR_LOG_PATH.open(
        "a",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=ERROR_FIELDS,
        )

        if not file_exists:
            writer.writeheader()

        writer.writerow(
            {
                "month": month,
                "source_type": source_type,
                "url": url,
                "error": str(error),
                "recorded_at": (
                    dt.datetime.now(
                        dt.timezone.utc
                    ).isoformat()
                ),
            }
        )


def add_current_month(
    output_records: dict[
        tuple[str, str],
        dict[str, str],
    ],
) -> None:
    """Add the current live gram and pavan records."""

    today = dt.date.today()
    current_month = month_identifier(
        today.year,
        today.month,
    )

    gram_url = (
        BASE_URL
        + "kerala-gold-rate-per-gram.htm"
    )

    pavan_url = (
        BASE_URL
        + "daily-gold-prices.htm"
    )

    print(
        f"\nAdding current live month: {current_month}"
    )

    try:
        gram_records = fetch_with_retries(
            gram_url,
            missing_is_allowed=False,
        )
    except Exception as exc:
        log_error(
            current_month,
            "current_gram",
            gram_url,
            exc,
        )
        raise

    try:
        pavan_records = fetch_with_retries(
            pavan_url,
            missing_is_allowed=False,
        )
    except Exception as exc:
        log_error(
            current_month,
            "current_pavan",
            pavan_url,
            exc,
        )
        raise

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
        gram_url,
        pavan_url,
    )

    for row in combined:
        key = (
            row["date"],
            row["session"],
        )

        output_records[key] = row

    print(
        f"  Current gram records: {len(gram_records)}"
    )
    print(
        f"  Current pavan records: {len(pavan_records)}"
    )
    print(
        f"  Current combined records: {len(combined)}"
    )


def print_validation_summary(
    output_records: dict[
        tuple[str, str],
        dict[str, str],
    ],
) -> None:
    """Print basic final data-quality statistics."""

    rows = sorted(
        output_records.values(),
        key=sort_key,
    )

    if not rows:
        print("\nNo records were collected.")
        return

    non_zero_differences = []

    missing_gram = 0
    missing_pavan = 0

    for row in rows:
        if not row["price_22k_1g_published"]:
            missing_gram += 1

        if not row["price_22k_8g_published"]:
            missing_pavan += 1

        difference_text = row["difference_22k"]

        if (
            difference_text
            and Decimal(difference_text) != 0
        ):
            non_zero_differences.append(row)

    print("\nBackfill validation summary")
    print("-" * 40)
    print(f"Total records: {len(rows)}")
    print(
        f"Date range: {rows[0]['date']} "
        f"to {rows[-1]['date']}"
    )
    print(
        f"Records without published gram price: "
        f"{missing_gram}"
    )
    print(
        f"Records without published pavan price: "
        f"{missing_pavan}"
    )
    print(
        f"Non-zero gram/pavan differences: "
        f"{len(non_zero_differences)}"
    )

    if non_zero_differences:
        print("\nFirst ten differences:")

        for row in non_zero_differences[:10]:
            print(
                f"  {row['date']} | "
                f"{row['session'] or 'Standard'} | "
                f"Published gram: "
                f"{row['price_22k_1g_published']} | "
                f"Pavan / 8: "
                f"{row['price_22k_1g_from_pavan']} | "
                f"Difference: {row['difference_22k']}"
            )


def main() -> int:
    archive_urls = load_pavan_archive_urls(
        ARCHIVE_LINKS_PATH
    )

    if not archive_urls:
        print(
            "No monthly pavan archive URLs were found."
        )
        return 1

    output_records = load_existing_output(
        OUTPUT_PATH
    )

    completed_months = load_completed_months(
        PROGRESS_PATH
    )

    print(
        f"Found {len(archive_urls)} monthly "
        "pavan archive pages."
    )

    print(
        f"Already completed months: "
        f"{len(completed_months)}"
    )

    print(
        f"Previously saved records: "
        f"{len(output_records)}"
    )

    for index, pavan_url in enumerate(
        archive_urls,
        start=1,
    ):
        year, month = parse_archive_month(
            pavan_url
        )

        month_id = month_identifier(
            year,
            month,
        )

        if month_id in completed_months:
            print(
                f"[{index}/{len(archive_urls)}] "
                f"Skipping {month_id}; already completed."
            )
            continue

        gram_url = make_gram_archive_url(
            pavan_url
        )

        print(
            f"\n[{index}/{len(archive_urls)}] "
            f"Processing {month_id}"
        )

        print(f"  Pavan: {pavan_url}")

        try:
            pavan_records = fetch_with_retries(
                pavan_url,
                missing_is_allowed=False,
            )
        except Exception as exc:
            print(f"  Pavan download failed: {exc}")

            log_error(
                month_id,
                "pavan",
                pavan_url,
                exc,
            )

            # The pavan archive is the known primary source.
            # Do not mark this month complete.
            continue

        pavan_records = validate_month_records(
            pavan_records,
            year,
            month,
            "pavan",
        )

        print(
            f"  Pavan records: "
            f"{len(pavan_records)}"
        )

        print(f"  Gram:  {gram_url}")

        try:
            gram_result = fetch_with_retries(
                gram_url,
                missing_is_allowed=True,
            )
        except Exception as exc:
            print(
                f"  Gram request failed; continuing "
                f"with pavan data: {exc}"
            )

            log_error(
                month_id,
                "gram",
                gram_url,
                exc,
            )

            gram_result = []

        if gram_result is None:
            print(
                "  Gram archive not available; "
                "using pavan / 8."
            )
            gram_records = []
        else:
            gram_records = validate_month_records(
                gram_result,
                year,
                month,
                "gram",
            )

            print(
                f"  Gram records: "
                f"{len(gram_records)}"
            )

        combined = combine_records(
            gram_records,
            pavan_records,
            gram_url,
            pavan_url,
        )

        if not combined:
            message = (
                "No valid combined records were produced."
            )

            print(f"  {message}")

            log_error(
                month_id,
                "combined",
                "",
                message,
            )

            continue

        for row in combined:
            key = (
                row["date"],
                row["session"],
            )

            output_records[key] = row

        # Save after every completed month so an interrupted
        # run can safely resume.
        save_output(output_records)

        append_progress(
            month_id,
            len(gram_records),
            len(pavan_records),
            len(combined),
        )

        completed_months.add(month_id)

        print(
            f"  Combined records saved: "
            f"{len(combined)}"
        )

        print(
            f"  Total saved records: "
            f"{len(output_records)}"
        )

    # Archive links normally stop at the previous completed
    # month, so add the current live month separately.
    add_current_month(output_records)

    save_output(output_records)

    print_validation_summary(
        output_records
    )

    print("\nFull backfill completed.")
    print(f"Output CSV: {OUTPUT_PATH}")
    print(f"Progress CSV: {PROGRESS_PATH}")
    print(f"Error log: {ERROR_LOG_PATH}")
    print(
        "Permanent history was not modified: "
        "data/gold_rates_history.csv"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())