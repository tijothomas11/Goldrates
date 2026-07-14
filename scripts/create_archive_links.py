"""Create archive_links.csv for all completed monthly pavan archives."""

import csv
import datetime as dt
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = PROJECT_ROOT / "archive_links.csv"

MONTH_NAMES = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]


def next_month(year: int, month: int) -> tuple[int, int]:
    """Return the month immediately following the input month."""

    if month == 12:
        return year + 1, 1

    return year, month + 1


def previous_month(year: int, month: int) -> tuple[int, int]:
    """Return the month immediately preceding the input month."""

    if month == 1:
        return year - 1, 12

    return year, month - 1


def main() -> int:
    today = dt.date.today()

    # Historical daily archives begin in November 2009.
    year = 2009
    month = 11

    # The current partial month is handled separately by
    # backfill_history.py using the live pages.
    final_year, final_month = previous_month(
        today.year,
        today.month,
    )

    # Build a list of month-based archive URLs from the first available
    # archive month up to the previous complete month.

    rows: list[dict[str, str]] = []

    while (year, month) <= (final_year, final_month):
        month_name = MONTH_NAMES[month - 1]

        url = (
            "https://www.keralagold.com/"
            f"daily-gold-prices-{month_name}-{year}.htm"
        )

        rows.append(
            {
                "link_text": (
                    f"{month_name.title()} {year}"
                ),
                "url": url,
            }
        )

        year, month = next_month(year, month)

    temporary_path = OUTPUT_PATH.with_suffix(".tmp.csv")

    with temporary_path.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["link_text", "url"],
        )

        writer.writeheader()
        writer.writerows(rows)

    temporary_path.replace(OUTPUT_PATH)

    print(f"Created: {OUTPUT_PATH.resolve()}")
    print(f"Monthly archive URLs: {len(rows)}")
    print(f"First URL: {rows[0]['url']}")
    print(f"Last URL: {rows[-1]['url']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())