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
    combine_incoming_records,
    compare_histories,
    count_unchanged_records,
    parse_historical_response,
    parse_historical_timestamp,
    parse_price,
    parse_recent_response,
    read_historical_browser_export,
    read_permanent_history,
)

class InternationalGoldUpdateTests(
    unittest.TestCase
):
    """Test parsing and immutable-history behavior."""

    def write_historical_export(
        self,
        path: Path,
        rows: list[dict[str, str]],
        fieldnames: list[str] | None = None,
    ) -> None:
        """Write a small historical fixture using the permanent schema."""

        with path.open(
            "w",
            encoding="utf-8",
            newline="",
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=(
                    fieldnames
                    if fieldnames is not None
                    else EXPECTED_FIELDS
                ),
                lineterminator="\n",
            )

            writer.writeheader()
            writer.writerows(rows)

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

    def test_combined_records_ignore_exact_duplicates(
        self
    ):
        """Identical timestamps and prices should occur once."""

        payload = json.dumps([
            "USD-XAU!,949716,64.9000"
        ])

        historical_records = (
            parse_historical_response(
                payload
            )
        )

        combined = combine_incoming_records(
            historical_records,
            historical_records,
        )

        self.assertEqual(
            len(combined),
            1,
        )

    def test_combined_records_reject_conflicts(
        self
    ):
        """Different prices at one timestamp must fail."""

        first = parse_historical_response(
            json.dumps([
                "USD-XAU!,949716,64.9000"
            ])
        )

        second = parse_historical_response(
            json.dumps([
                "USD-XAU!,949716,65.0000"
            ])
        )

        with self.assertRaises(ValueError):
            combine_incoming_records(
                first,
                second,
            )

    def test_count_unchanged_records(self):
        """Only exact permanent matches are unchanged."""

        incoming = parse_historical_response(
            json.dumps([
                (
                    "USD-XAU!,"
                    "949716,64.9000,"
                    "950580,65.2500"
                )
            ])
        )

        permanent = {
            "1973-01-04T05:00:00.000Z":
                Decimal("64.9000"),
        }

        actual = count_unchanged_records(
            permanent,
            incoming,
        )

        self.assertEqual(
            actual,
            1,
        )

    def test_historical_browser_export_is_parsed(self):
        """A valid historical CSV should become normalized records."""

        with tempfile.TemporaryDirectory() as directory:
            path = (
                Path(directory)
                / "historical-preview.csv"
            )

            first_ounce_price = Decimal(
                "64.9000"
            )

            second_ounce_price = Decimal(
                "65.2500"
            )

            self.write_historical_export(
                path,
                [
                    {
                        "timestamp_utc":
                            "1973-01-04T05:00:00.000Z",
                        "date":
                            "1973-01-04",
                        "price_usd_per_troy_ounce":
                            str(first_ounce_price),
                        "price_usd_per_gram":
                            (
                                f"{calculate_usd_per_gram(first_ounce_price):.6f}"
                            ),
                        "source":
                            "GoldPrice.org",
                    },
                    {
                        "timestamp_utc":
                            "1973-01-05T05:00:00.000Z",
                        "date":
                            "1973-01-05",
                        "price_usd_per_troy_ounce":
                            str(second_ounce_price),
                        "price_usd_per_gram":
                            (
                                f"{calculate_usd_per_gram(second_ounce_price):.6f}"
                            ),
                        "source":
                            "GoldPrice.org",
                    },
                ],
            )

            records = (
                read_historical_browser_export(
                    path
                )
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
            records[0]["usd_per_troy_ounce"],
            Decimal("64.9000"),
        )

        self.assertEqual(
            records[1]["timestamp_utc"],
            "1973-01-05T05:00:00.000Z",
        )

        self.assertEqual(
            records[1]["source"],
            "GoldPrice.org",
        )

    def test_historical_browser_export_rejects_wrong_columns(
        self
    ):
        """A historical file with the wrong schema must fail."""

        with tempfile.TemporaryDirectory() as directory:
            path = (
                Path(directory)
                / "historical-preview.csv"
            )

            wrong_fields = [
                "timestamp_utc",
                "date",
                "price",
                "source",
            ]

            self.write_historical_export(
                path,
                [
                    {
                        "timestamp_utc":
                            "1973-01-04T05:00:00.000Z",
                        "date":
                            "1973-01-04",
                        "price":
                            "64.9000",
                        "source":
                            "GoldPrice.org",
                    }
                ],
                fieldnames=wrong_fields,
            )

            with self.assertRaises(ValueError):
                read_historical_browser_export(
                    path
                )

    def test_historical_browser_export_rejects_wrong_source(
        self
    ):
        """Historical observations must retain GoldPrice.org attribution."""

        with tempfile.TemporaryDirectory() as directory:
            path = (
                Path(directory)
                / "historical-preview.csv"
            )

            ounce_price = Decimal(
                "64.9000"
            )

            self.write_historical_export(
                path,
                [
                    {
                        "timestamp_utc":
                            "1973-01-04T05:00:00.000Z",
                        "date":
                            "1973-01-04",
                        "price_usd_per_troy_ounce":
                            str(ounce_price),
                        "price_usd_per_gram":
                            (
                                f"{calculate_usd_per_gram(ounce_price):.6f}"
                            ),
                        "source":
                            "Unexpected source",
                    }
                ],
            )

            with self.assertRaises(ValueError):
                read_historical_browser_export(
                    path
                )

    def test_historical_browser_export_rejects_wrong_conversion(
        self
    ):
        """A modified ounce-to-gram calculation must fail validation."""

        with tempfile.TemporaryDirectory() as directory:
            path = (
                Path(directory)
                / "historical-preview.csv"
            )

            self.write_historical_export(
                path,
                [
                    {
                        "timestamp_utc":
                            "1973-01-04T05:00:00.000Z",
                        "date":
                            "1973-01-04",
                        "price_usd_per_troy_ounce":
                            "64.9000",
                        "price_usd_per_gram":
                            "999.000000",
                        "source":
                            "GoldPrice.org",
                    }
                ],
            )

            with self.assertRaises(ValueError):
                read_historical_browser_export(
                    path
                )

    def test_historical_and_recent_records_are_combined_in_order(
        self
    ):
        """Catch-up records should be sorted before newer recent records."""

        with tempfile.TemporaryDirectory() as directory:
            path = (
                Path(directory)
                / "historical-preview.csv"
            )

            ounce_price = Decimal(
                "64.9000"
            )

            self.write_historical_export(
                path,
                [
                    {
                        "timestamp_utc":
                            "1973-01-04T05:00:00.000Z",
                        "date":
                            "1973-01-04",
                        "price_usd_per_troy_ounce":
                            str(ounce_price),
                        "price_usd_per_gram":
                            (
                                f"{calculate_usd_per_gram(ounce_price):.6f}"
                            ),
                        "source":
                            "GoldPrice.org",
                    }
                ],
            )

            historical_records = (
                read_historical_browser_export(
                    path
                )
            )

        recent_records = parse_recent_response(
            json.dumps([
                "USD-XAU,100.00,101.00"
            ]),
            datetime(
                2026,
                7,
                21,
                20,
                17,
                9,
                574000,
                tzinfo=timezone.utc,
            ),
        )

        combined = combine_incoming_records(
            historical_records,
            recent_records,
        )

        self.assertEqual(
            len(combined),
            3,
        )

        self.assertEqual(
            combined[0]["timestamp_utc"],
            "1973-01-04T05:00:00.000Z",
        )

        self.assertEqual(
            combined[-1]["timestamp_utc"],
            "2026-07-21T20:16:59.574Z",
        )

    def test_historical_and_recent_records_reject_conflict(
        self
    ):
        """Two inputs cannot assign different prices to one timestamp."""

        historical_records = (
            parse_historical_response(
                json.dumps([
                    "USD-XAU!,949716,64.9000"
                ])
            )
        )

        conflicting_records = (
            parse_historical_response(
                json.dumps([
                    "USD-XAU!,949716,65.0000"
                ])
            )
        )

        with self.assertRaises(ValueError):
            combine_incoming_records(
                historical_records,
                conflicting_records,
            )

if __name__ == "__main__":
    unittest.main()