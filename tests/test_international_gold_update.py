"""Tests for the international gold update foundation."""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIRECTORY = PROJECT_ROOT / "scripts"

# Allow tests to import scripts without installing
# the scripts directory as a Python package.
sys.path.insert(
    0,
    str(SCRIPTS_DIRECTORY),
)


from update_international_gold import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    EXPECTED_FIELDS,
    calculate_usd_per_gram,
    compare_histories,
    parse_historical_response,
    parse_historical_timestamp,
    parse_price,
    parse_recent_response,
    read_permanent_history,
)


class InternationalGoldUpdateTests(
    unittest.TestCase
):
    """Test parsing and immutable-history behavior."""

    def test_usd_per_gram_formula_is_exact(self):
        """The documented divisor should be used."""

        ounce_price = Decimal("4083.7400")

        actual = calculate_usd_per_gram(
            ounce_price
        )

        expected = (
            ounce_price /
            Decimal("31.1034768")
        )

        self.assertEqual(
            actual,
            expected,
        )

    def test_usd_per_gram_rejects_invalid_prices(
        self
    ):
        """Impossible prices must be rejected."""

        invalid_prices = [
            Decimal("0"),
            Decimal("-1"),
            Decimal("Infinity"),
            Decimal("-Infinity"),
            Decimal("NaN"),
        ]

        for invalid_price in invalid_prices:
            with self.subTest(
                invalid_price=invalid_price
            ):
                with self.assertRaises(
                    ValueError
                ):
                    calculate_usd_per_gram(
                        invalid_price
                    )

    def test_parse_price_accepts_valid_price(self):
        """Valid text should become an exact Decimal."""

        self.assertEqual(
            parse_price(" 4083.7400 "),
            Decimal("4083.7400"),
        )

    def test_parse_price_rejects_invalid_text(self):
        """Invalid price text must be rejected."""

        invalid_values = [
            "",
            "not-a-price",
            "NaN",
            "Infinity",
            "0",
            "-1",
        ]

        for invalid_value in invalid_values:
            with self.subTest(
                invalid_value=invalid_value
            ):
                with self.assertRaises(
                    ValueError
                ):
                    parse_price(
                        invalid_value
                    )

    def test_historical_timestamp_conversion(self):
        """A known time code should produce UTC."""

        actual = parse_historical_timestamp(
            "949716"
        )

        expected = datetime(
            1973,
            1,
            4,
            5,
            0,
            0,
            tzinfo=timezone.utc,
        )

        self.assertEqual(
            actual,
            expected,
        )

    def test_historical_timestamp_rejects_invalid_codes(
        self
    ):
        """Invalid time codes must be rejected."""

        invalid_codes = [
            "",
            "not-a-code",
            "0",
            "-1",
            "NaN",
            "Infinity",
        ]

        for invalid_code in invalid_codes:
            with self.subTest(
                invalid_code=invalid_code
            ):
                with self.assertRaises(
                    ValueError
                ):
                    parse_historical_timestamp(
                        invalid_code
                    )

    def test_parse_historical_response(self):
        """Historical pairs should become records."""

        payload = json.dumps([
            (
                "USD-XAU!,"
                "949716,64.9000,"
                "950580,65.2500"
            )
        ])

        records = parse_historical_response(
            payload
        )

        self.assertEqual(
            len(records),
            2,
        )

        self.assertEqual(
            records[0]["timestamp_utc"],
            "1973-01-04T05:00:00.000Z",
        )

        self.assertEqual(
            records[0]["date"],
            "1973-01-04",
        )

        self.assertEqual(
            records[0]["usd_per_troy_ounce"],
            Decimal("64.9000"),
        )

        self.assertEqual(
            records[0]["source"],
            "GoldPrice.org",
        )

    def test_historical_response_rejects_unpaired_values(
        self
    ):
        """A missing price must cause failure."""

        payload = json.dumps([
            "USD-XAU!,949716,64.9000,950580"
        ])

        with self.assertRaises(ValueError):
            parse_historical_response(
                payload
            )

    def test_recent_response_reconstructs_timestamps(
        self
    ):
        """Recent observations should be ten seconds apart."""

        payload = json.dumps([
            "USD-XAU,100.00,101.00,102.00"
        ])

        retrieval_timestamp = datetime(
            2026,
            7,
            21,
            20,
            17,
            9,
            574000,
            tzinfo=timezone.utc,
        )

        records = parse_recent_response(
            payload,
            retrieval_timestamp,
        )

        self.assertEqual(
            [
                record["timestamp_utc"]
                for record in records
            ],
            [
                "2026-07-21T20:16:39.574Z",
                "2026-07-21T20:16:49.574Z",
                "2026-07-21T20:16:59.574Z",
            ],
        )

    def test_recent_response_requires_timezone(
        self
    ):
        """A local or ambiguous retrieval time must fail."""

        payload = json.dumps([
            "USD-XAU,100.00"
        ])

        retrieval_timestamp = datetime(
            2026,
            7,
            21,
            20,
            17,
            9,
        )

        with self.assertRaises(ValueError):
            parse_recent_response(
                payload,
                retrieval_timestamp,
            )

    def test_compare_histories_finds_changes(self):
        """New timestamps and conflicts must be separate."""

        permanent = {
            "1973-01-04T05:00:00.000Z":
                Decimal("64.9000"),
            "1973-01-05T05:00:00.000Z":
                Decimal("65.0000"),
        }

        incoming = parse_historical_response(
            json.dumps([
                (
                    "USD-XAU!,"
                    "949716,64.9000,"
                    "950580,65.2500,"
                    "951444,66.0000"
                )
            ])
        )

        additions, conflicts = compare_histories(
            permanent,
            incoming,
        )

        self.assertEqual(
            len(additions),
            1,
        )

        self.assertEqual(
            additions[0]["timestamp_utc"],
            "1973-01-06T05:00:00.000Z",
        )

        self.assertEqual(
            conflicts,
            [
                (
                    "1973-01-05T05:00:00.000Z",
                    Decimal("65.0000"),
                    Decimal("65.2500"),
                )
            ],
        )

    def test_read_permanent_history(self):
        """Permanent records should retain exact prices."""

        with tempfile.TemporaryDirectory() as directory:
            path = (
                Path(directory) /
                "international_gold_spot.csv"
            )

            with path.open(
                "w",
                encoding="utf-8",
                newline="",
            ) as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=EXPECTED_FIELDS,
                )

                writer.writeheader()

                writer.writerow({
                    "timestamp_utc":
                        "1973-01-04T05:00:00.000Z",
                    "date":
                        "1973-01-04",
                    "price_usd_per_troy_ounce":
                        "64.9000",
                    "price_usd_per_gram":
                        "2.086590",
                    "source":
                        "GoldPrice.org",
                })

            actual = read_permanent_history(
                path
            )

            self.assertEqual(
                actual,
                {
                    "1973-01-04T05:00:00.000Z":
                        Decimal("64.9000")
                },
            )


if __name__ == "__main__":
    unittest.main()