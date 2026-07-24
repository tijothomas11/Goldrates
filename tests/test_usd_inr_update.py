"""Tests for the safe USD/INR update pipeline."""

from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIRECTORY = PROJECT_ROOT / "scripts"

# Allow the test to import the command-line scripts
# without installing them as a Python package.
sys.path.insert(
    0,
    str(SCRIPTS_DIRECTORY),
)

from update_usd_inr import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    compare_histories,
    read_raw_fred_file,
    write_history,
)

from validate_usd_inr import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    EXPECTED_FIELDS,
    EXPECTED_SERIES,
    EXPECTED_SOURCE,
    validate_file,
)


class UsdInrUpdateTests(unittest.TestCase):
    """Test parsing, comparison, and safe output behavior."""

    def setUp(self) -> None:
        """Create an isolated temporary directory."""

        self.temporary_directory = (
            tempfile.TemporaryDirectory()
        )

        self.temp_path = Path(
            self.temporary_directory.name
        )

    def tearDown(self) -> None:
        """Remove all temporary test files."""

        self.temporary_directory.cleanup()

    def write_raw_csv(
        self,
        rows: list[tuple[str, str]],
    ) -> Path:
        """Create a raw FRED-format CSV fixture."""

        path = (
            self.temp_path
            / "DEXINUS.csv"
        )

        with path.open(
            "w",
            encoding="utf-8",
            newline="",
        ) as file:
            writer = csv.writer(file)

            writer.writerow([
                "observation_date",
                "DEXINUS",
            ])

            writer.writerows(rows)

        return path

    def test_identical_records_produce_no_changes(
        self,
    ) -> None:
        """Existing matching rates should be ignored."""

        permanent = {
            "2026-07-16": Decimal("96.3500"),
            "2026-07-17": Decimal("96.2800"),
        }

        incoming = {
            "2026-07-16": Decimal("96.3500"),
            "2026-07-17": Decimal("96.2800"),
        }

        additions, conflicts = compare_histories(
            permanent,
            incoming,
        )

        self.assertEqual(additions, {})
        self.assertEqual(conflicts, [])

    def test_new_dates_are_reported_as_additions(
        self,
    ) -> None:
        """New observations should be detected."""

        permanent = {
            "2026-07-17": Decimal("96.2800"),
        }

        incoming = {
            "2026-07-17": Decimal("96.2800"),
            "2026-07-20": Decimal("96.4100"),
        }

        additions, conflicts = compare_histories(
            permanent,
            incoming,
        )

        self.assertEqual(
            additions,
            {
                "2026-07-20":
                    Decimal("96.4100"),
            },
        )

        self.assertEqual(conflicts, [])

    def test_changed_historical_value_is_conflict(
        self,
    ) -> None:
        """An existing date must never be overwritten."""

        permanent = {
            "2026-07-17": Decimal("96.2800"),
        }

        incoming = {
            "2026-07-17": Decimal("96.9900"),
        }

        additions, conflicts = compare_histories(
            permanent,
            incoming,
        )

        self.assertEqual(additions, {})

        self.assertEqual(
            conflicts,
            [
                (
                    "2026-07-17",
                    Decimal("96.2800"),
                    Decimal("96.9900"),
                )
            ],
        )

    def test_missing_fred_values_are_ignored(
        self,
    ) -> None:
        """Blank and period values are not prices."""

        raw_path = self.write_raw_csv([
            ("2026-07-14", "96.2000"),
            ("2026-07-15", "."),
            ("2026-07-16", ""),
            ("2026-07-17", "96.2800"),
        ])

        observations = read_raw_fred_file(
            raw_path
        )

        self.assertEqual(
            observations,
            {
                "2026-07-14":
                    Decimal("96.2000"),
                "2026-07-17":
                    Decimal("96.2800"),
            },
        )

    def test_conflicting_duplicate_raw_date_fails(
        self,
    ) -> None:
        """A raw file cannot assign two rates to one date."""

        raw_path = self.write_raw_csv([
            ("2026-07-17", "96.2800"),
            ("2026-07-17", "96.9900"),
        ])

        with self.assertRaisesRegex(
            ValueError,
            "conflicting values",
        ):
            read_raw_fred_file(raw_path)

    def test_written_history_is_sorted_and_valid(
        self,
    ) -> None:
        """Output must be chronological and attributable."""

        output_path = (
            self.temp_path
            / "usd_inr_history.csv"
        )

        observations = {
            "2026-07-17": Decimal("96.2800"),
            "1973-01-02": Decimal("8.0200"),
            "2000-01-03": Decimal("43.5500"),
        }

        write_history(
            output_path,
            observations,
        )

        with output_path.open(
            "r",
            encoding="utf-8",
            newline="",
        ) as file:
            rows = list(
                csv.DictReader(file)
            )

        self.assertEqual(
            [row["date"] for row in rows],
            [
                "1973-01-02",
                "2000-01-03",
                "2026-07-17",
            ],
        )

        self.assertEqual(
            rows[0]["source"],
            EXPECTED_SOURCE,
        )

        self.assertEqual(
            rows[0]["series"],
            EXPECTED_SERIES,
        )

        errors, warnings, validated_rows = (
            validate_file(output_path)
        )

        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        self.assertEqual(
            len(validated_rows),
            3,
        )

    def test_validator_rejects_duplicate_dates(
        self,
    ) -> None:
        """Permanent history cannot contain duplicate dates."""

        output_path = (
            self.temp_path
            / "usd_inr_history.csv"
        )

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

            writer.writerow({
                "date": "2026-07-17",
                "usd_inr": "96.2800",
                "source": EXPECTED_SOURCE,
                "series": EXPECTED_SERIES,
            })

            writer.writerow({
                "date": "2026-07-17",
                "usd_inr": "96.2800",
                "source": EXPECTED_SOURCE,
                "series": EXPECTED_SERIES,
            })

        errors, _, _ = validate_file(
            output_path
        )

        self.assertTrue(
            any(
                "duplicate date" in error
                for error in errors
            )
        )


if __name__ == "__main__":
    unittest.main()