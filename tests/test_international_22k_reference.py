"""Tests for the international-derived 22K INR reference."""

from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIRECTORY = PROJECT_ROOT / "scripts"

# The command-line scripts are not installed as a package.
# Add the scripts directory for these isolated tests.
sys.path.insert(
    0,
    str(SCRIPTS_DIRECTORY),
)


from generate_international_22k_reference import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    MAX_FX_GAP_DAYS,
    PURITY_FACTOR_22K,
    TROY_OUNCE_GRAMS,
    find_fx_record,
    generate_rows,
    validate_output_rows,
    write_output,
)

from validate_international_22k_reference import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    EXPECTED_FIELDS,
    validate_file,
)


class International22kReferenceTests(
    unittest.TestCase
):
    """Test FX matching and derived-price calculations."""

    def setUp(self) -> None:
        """Create an isolated temporary directory."""

        self.temporary_directory = (
            tempfile.TemporaryDirectory()
        )

        self.temp_path = Path(
            self.temporary_directory.name
        )

    def tearDown(self) -> None:
        """Remove the temporary test directory."""

        self.temporary_directory.cleanup()

    def make_gold_record(
        self,
        date_text: str,
        usd_per_ounce: str = "4000.0000",
    ) -> dict[str, object]:
        """Create one representative gold record."""

        timestamp = datetime.fromisoformat(
            f"{date_text}T12:00:00+00:00"
        )

        return {
            "timestamp": timestamp,
            "timestamp_text":
                timestamp.isoformat().replace(
                    "+00:00",
                    "Z",
                ),
            "date": date.fromisoformat(
                date_text
            ),
            "usd_per_ounce":
                Decimal(usd_per_ounce),
            "source": "GoldPrice.org",
        }

    def make_fx_record(
        self,
        date_text: str,
        usd_inr: str,
    ) -> dict[str, object]:
        """Create one representative FX record."""

        return {
            "date": date.fromisoformat(
                date_text
            ),
            "usd_inr": Decimal(usd_inr),
            "source":
                "Federal Reserve Board via FRED",
            "series": "DEXINUS",
        }

    def test_same_day_fx_rate_is_used(
        self,
    ) -> None:
        """A same-day exchange rate should have a zero gap."""

        gold_records = [
            self.make_gold_record(
                "2026-07-17"
            )
        ]

        fx_records = [
            self.make_fx_record(
                "2026-07-16",
                "96.3500",
            ),
            self.make_fx_record(
                "2026-07-17",
                "96.2800",
            ),
        ]

        rows, warnings = generate_rows(
            gold_records,
            fx_records,
        )

        self.assertEqual(warnings, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["fx_date"],
            "2026-07-17",
        )
        self.assertEqual(
            rows[0]["fx_gap_days"],
            "0",
        )
        self.assertEqual(
            rows[0]["usd_inr"],
            "96.2800",
        )

    def test_latest_earlier_fx_rate_is_used(
        self,
    ) -> None:
        """A weekend or holiday uses the latest earlier rate."""

        gold_records = [
            self.make_gold_record(
                "2026-07-19"
            )
        ]

        fx_records = [
            self.make_fx_record(
                "2026-07-16",
                "96.3500",
            ),
            self.make_fx_record(
                "2026-07-17",
                "96.2800",
            ),
        ]

        rows, warnings = generate_rows(
            gold_records,
            fx_records,
        )

        self.assertEqual(warnings, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["fx_date"],
            "2026-07-17",
        )
        self.assertEqual(
            rows[0]["fx_gap_days"],
            "2",
        )

    def test_future_fx_rate_is_never_selected(
        self,
    ) -> None:
        """FX selection must never look ahead in time."""

        fx_records = [
            self.make_fx_record(
                "2026-07-17",
                "96.2800",
            ),
            self.make_fx_record(
                "2026-07-21",
                "96.5000",
            ),
        ]

        fx_dates = [
            record["date"]
            for record in fx_records
        ]

        selected_record = find_fx_record(
            date.fromisoformat(
                "2026-07-20"
            ),
            fx_dates,
            fx_records,
        )

        self.assertIsNotNone(
            selected_record
        )

        self.assertEqual(
            selected_record["date"],
            date.fromisoformat(
                "2026-07-17"
            ),
        )

    def test_stale_fx_rate_is_skipped(
        self,
    ) -> None:
        """An exchange rate older than seven days is rejected."""

        gold_records = [
            self.make_gold_record(
                "2026-07-20"
            )
        ]

        fx_records = [
            self.make_fx_record(
                "2026-07-12",
                "96.1000",
            )
        ]

        rows, warnings = generate_rows(
            gold_records,
            fx_records,
        )

        self.assertEqual(rows, [])
        self.assertEqual(
            len(warnings),
            1,
        )
        self.assertIn(
            str(MAX_FX_GAP_DAYS),
            warnings[0],
        )

    def test_reference_formula_is_exact(
        self,
    ) -> None:
        """The stored values should follow the documented formula."""

        usd_per_ounce = Decimal(
            "4083.7400"
        )

        usd_inr = Decimal(
            "96.2800"
        )

        gold_records = [
            self.make_gold_record(
                "2026-07-21",
                str(usd_per_ounce),
            )
        ]

        fx_records = [
            self.make_fx_record(
                "2026-07-17",
                str(usd_inr),
            )
        ]

        rows, warnings = generate_rows(
            gold_records,
            fx_records,
        )

        self.assertEqual(warnings, [])
        self.assertEqual(len(rows), 1)

        expected_24k = (
            usd_per_ounce
            / TROY_OUNCE_GRAMS
            * usd_inr
        )

        expected_22k = (
            expected_24k
            * PURITY_FACTOR_22K
        )

        self.assertEqual(
            rows[0][
                "reference_24k_inr_per_gram"
            ],
            f"{expected_24k:.4f}",
        )

        self.assertEqual(
            rows[0][
                "reference_22k_inr_per_gram"
            ],
            f"{expected_22k:.4f}",
        )

    def test_output_validator_accepts_valid_file(
        self,
    ) -> None:
        """A correctly generated file should pass validation."""

        gold_records = [
            self.make_gold_record(
                "2026-07-17",
                "4002.4900",
            ),
            self.make_gold_record(
                "2026-07-18",
                "4010.0000",
            ),
        ]

        fx_records = [
            self.make_fx_record(
                "2026-07-17",
                "96.2800",
            )
        ]

        rows, warnings = generate_rows(
            gold_records,
            fx_records,
        )

        self.assertEqual(warnings, [])

        validate_output_rows(rows)

        output_path = (
            self.temp_path
            / "international_gold_22k_inr.csv"
        )

        write_output(
            output_path,
            rows,
        )

        errors, file_warnings, validated_rows = (
            validate_file(output_path)
        )

        self.assertEqual(errors, [])
        self.assertEqual(
            file_warnings,
            [],
        )
        self.assertEqual(
            len(validated_rows),
            2,
        )

    def test_validator_rejects_future_fx_date(
        self,
    ) -> None:
        """A derived file must never use a future FX rate."""

        output_path = (
            self.temp_path
            / "invalid_reference.csv"
        )

        row = {
            "gold_timestamp_utc":
                "2026-07-17T12:00:00Z",
            "gold_date":
                "2026-07-17",
            "fx_date":
                "2026-07-18",
            "fx_gap_days":
                "-1",
            "usd_per_troy_ounce":
                "4002.4900",
            "usd_inr":
                "96.2800",
            "reference_24k_inr_per_gram":
                "12390.0000",
            "reference_22k_inr_per_gram":
                "11357.5000",
            "gold_source":
                "GoldPrice.org",
            "fx_source":
                "Federal Reserve Board via FRED",
            "fx_series":
                "DEXINUS",
        }

        with output_path.open(
            "w",
            encoding="utf-8",
            newline="",
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=EXPECTED_FIELDS,
            )

            writer.writeheader()
            writer.writerow(row)

        errors, _, _ = validate_file(
            output_path
        )

        self.assertTrue(
            any(
                "future FX rate" in error
                for error in errors
            )
        )

    def test_validator_rejects_wrong_formula(
        self,
    ) -> None:
        """A manually altered reference value must fail."""

        gold_records = [
            self.make_gold_record(
                "2026-07-17",
                "4002.4900",
            )
        ]

        fx_records = [
            self.make_fx_record(
                "2026-07-17",
                "96.2800",
            )
        ]

        rows, warnings = generate_rows(
            gold_records,
            fx_records,
        )

        self.assertEqual(warnings, [])

        rows[0][
            "reference_22k_inr_per_gram"
        ] = "9999.0000"

        output_path = (
            self.temp_path
            / "wrong_formula.csv"
        )

        write_output(
            output_path,
            rows,
        )

        errors, _, _ = validate_file(
            output_path
        )

        self.assertTrue(
            any(
                "incorrect 22K reference" in error
                for error in errors
            )
        )

    def test_duplicate_timestamps_are_rejected(
        self,
    ) -> None:
        """Derived output cannot repeat a gold timestamp."""

        gold_record = self.make_gold_record(
            "2026-07-17",
            "4002.4900",
        )

        gold_records = [
            gold_record,
            dict(gold_record),
        ]

        fx_records = [
            self.make_fx_record(
                "2026-07-17",
                "96.2800",
            )
        ]

        rows, warnings = generate_rows(
            gold_records,
            fx_records,
        )

        self.assertEqual(warnings, [])

        with self.assertRaisesRegex(
            ValueError,
            "duplicate gold timestamp",
        ):
            validate_output_rows(rows)


if __name__ == "__main__":
    unittest.main()