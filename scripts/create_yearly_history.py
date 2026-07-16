"""Create a preview CSV of yearly Kerala gold reference prices.

The KeralaGold yearly table publishes the price of one pavan,
or eight grams of 22K gold, on March 31 for selected years.

This script creates:

    data/gold_rates_yearly_preview.csv

It does not modify:

    data/gold_rates_history.csv
"""

from __future__ import annotations

import csv
import datetime as dt
import re
import sys
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from goldrate_tracker import fetch_html


SOURCE_URL = (
    "https://www.keralagold.com/"
    "yearly-gold-prices.htm"
)

OUTPUT_PATH = (
    DATA_DIR / "gold_rates_yearly_preview.csv"
)

OUTPUT_FIELDS = [
    "year",
    "date",
    "price_22k_8g_published",
    "price_22k_1g_calculated",
    "source_url",
]

PAVAN_GRAMS = Decimal("8")


class YearlyTableParser(HTMLParser):
    """Collect the text from cells in every HTML table row."""

    def __init__(self):
        super().__init__()

        self.rows = []
        self.current_row = []
        self.current_cell = []

        self.in_row = False
        self.in_cell = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()

        if tag == "tr":
            self.in_row = True
            self.current_row = []

        elif tag in {"td", "th"} and self.in_row:
            self.in_cell = True
            self.current_cell = []

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag in {"td", "th"} and self.in_cell:
            cell_text = " ".join(
                part.strip()
                for part in self.current_cell
                if part.strip()
            )

            self.current_row.append(cell_text)

            self.current_cell = []
            self.in_cell = False

        elif tag == "tr" and self.in_row:
            if self.current_row:
                self.rows.append(
                    self.current_row
                )

            self.current_row = []
            self.in_row = False
            self.in_cell = False

    def handle_data(self, data):
        if self.in_cell:
            self.current_cell.append(data)


def parse_decimal(text):
    """Convert a published price string to Decimal."""

    cleaned = (
        text.strip()
        .replace(",", "")
        .replace("Rs.", "")
        .replace("Rs", "")
        .replace("\xa0", "")
    )

    match = re.search(
        r"\d+(?:\.\d+)?",
        cleaned,
    )

    if not match:
        raise ValueError(
            f"Could not parse price: {text!r}"
        )

    try:
        return Decimal(
            match.group(0)
        )

    except InvalidOperation as exc:
        raise ValueError(
            f"Invalid price: {text!r}"
        ) from exc


def decimal_text(value):
    """Return a plain decimal string for CSV output."""

    text = format(value, "f")

    if "." in text:
        text = text.rstrip("0")
        text = text.rstrip(".")

    return text


def is_march_31(date_text):
    """Return whether a source date represents March 31."""

    normalized = re.sub(
        r"\s+",
        "",
        date_text,
    )

    return bool(
        re.search(
            r"31-?March-?\d{2,4}",
            normalized,
            re.IGNORECASE,
        )
    )


def parse_yearly_history(html):
    """Extract yearly March 31 reference observations."""

    parser = YearlyTableParser()
    parser.feed(html)

    observations = []
    seen_years = set()

    current_year = dt.date.today().year

    for row in parser.rows:
        if len(row) < 3:
            continue

        year_text = row[0].strip()
        date_text = row[1].strip()
        price_text = row[2].strip()

        if not re.fullmatch(
            r"\d{4}",
            year_text,
        ):
            continue

        year = int(year_text)

        if year < 1900:
            continue

        if year > current_year:
            continue

        if not is_march_31(date_text):
            continue

        if year in seen_years:
            raise ValueError(
                "Duplicate yearly observation: "
                f"{year}"
            )

        pavan_price = parse_decimal(
            price_text
        )

        if pavan_price <= 0:
            raise ValueError(
                "Invalid pavan price for "
                f"{year}: {pavan_price}"
            )

        gram_price = (
            pavan_price / PAVAN_GRAMS
        )

        observations.append(
            {
                "year": str(year),
                "date": f"{year:04d}-03-31",
                "price_22k_8g_published": (
                    decimal_text(pavan_price)
                ),
                "price_22k_1g_calculated": (
                    decimal_text(gram_price)
                ),
                "source_url": SOURCE_URL,
            }
        )

        seen_years.add(year)

    observations.sort(
        key=lambda item: int(
            item["year"]
        )
    )

    return observations


def validate_observations(rows):
    """Validate all extracted yearly observations."""

    if not rows:
        raise ValueError(
            "No yearly observations were found."
        )

    years = [
        int(row["year"])
        for row in rows
    ]

    if len(years) != len(set(years)):
        raise ValueError(
            "Duplicate years were found."
        )

    if years != sorted(years):
        raise ValueError(
            "Yearly observations are not sorted."
        )

    for row in rows:
        year = int(row["year"])

        expected_date = (
            f"{year:04d}-03-31"
        )

        if row["date"] != expected_date:
            raise ValueError(
                "Unexpected date for "
                f"{year}: {row['date']}"
            )

        pavan_price = Decimal(
            row[
                "price_22k_8g_published"
            ]
        )

        gram_price = Decimal(
            row[
                "price_22k_1g_calculated"
            ]
        )

        expected_gram = (
            pavan_price / PAVAN_GRAMS
        )

        if gram_price != expected_gram:
            raise ValueError(
                "Incorrect gram calculation "
                f"for {year}."
            )

        if row["source_url"] != SOURCE_URL:
            raise ValueError(
                "Unexpected source URL for "
                f"{year}."
            )


def write_csv(rows):
    """Write the yearly preview through a temporary file."""

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = (
        OUTPUT_PATH.with_suffix(
            OUTPUT_PATH.suffix + ".tmp"
        )
    )

    try:
        with temporary_path.open(
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

        temporary_path.replace(
            OUTPUT_PATH
        )

    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()

        raise


def main():
    """Fetch, parse, validate and write the yearly preview."""

    print(
        "Fetching yearly gold-price history..."
    )

    print(
        f"Source: {SOURCE_URL}"
    )

    try:
        html = fetch_html(
            SOURCE_URL
        )

        observations = (
            parse_yearly_history(html)
        )

        validate_observations(
            observations
        )

        write_csv(
            observations
        )

    except Exception as exc:
        print(
            "Yearly-history preview failed: "
            f"{exc}"
        )

        return 1

    first = observations[0]
    last = observations[-1]

    print("")
    print(
        "Yearly-history preview created."
    )

    print(
        f"Observations: "
        f"{len(observations)}"
    )

    print(
        "First observation: "
        f"{first['date']} | "
        "pavan="
        f"{first['price_22k_8g_published']} | "
        "gram="
        f"{first['price_22k_1g_calculated']}"
    )

    print(
        "Last observation: "
        f"{last['date']} | "
        "pavan="
        f"{last['price_22k_8g_published']} | "
        "gram="
        f"{last['price_22k_1g_calculated']}"
    )

    print(
        f"Preview file: {OUTPUT_PATH}"
    )

    print("")
    print(
        "The permanent daily history was not modified."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())