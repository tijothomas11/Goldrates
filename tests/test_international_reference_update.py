"""Tests for the international-reference workflow coordinator."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import call, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIRECTORY = PROJECT_ROOT / "scripts"

# The scripts directory is not a formal Python package,
# so add it explicitly for direct unit testing.
sys.path.insert(
    0,
    str(SCRIPTS_DIRECTORY),
)


import update_international_reference as coordinator  # noqa: E402  # pyright: ignore[reportMissingImports]


class InternationalReferenceCoordinatorTests(
    unittest.TestCase
):
    """Protect command ordering, preview safety, and rollback behavior."""
    def test_two_days_old_uses_recent_only(self):
        """Two days remains within the recent-only route."""

        latest = datetime(
            2026,
            8,
            1,
            tzinfo=timezone.utc,
        )

        route, reasons = (
            coordinator.select_update_route(
                latest,
                latest - timedelta(hours=1),
                latest + timedelta(days=2),
            )
        )

        self.assertEqual(
            route,
            coordinator.RECENT_ONLY_ROUTE,
        )

        self.assertEqual(
            reasons,
            [],
        )

    def test_exactly_four_days_uses_recent_only(self):
        """Exactly four days is allowed in recent-only mode."""

        latest = datetime(
            2026,
            8,
            1,
            tzinfo=timezone.utc,
        )

        route, reasons = (
            coordinator.select_update_route(
                latest,
                latest - timedelta(hours=1),
                latest + timedelta(days=4),
            )
        )

        self.assertEqual(
            route,
            coordinator.RECENT_ONLY_ROUTE,
        )

        self.assertEqual(
            reasons,
            [],
        )

    def test_more_than_four_days_requires_catchup(self):
        """One millisecond beyond four days requires catch-up."""

        latest = datetime(
            2026,
            8,
            1,
            tzinfo=timezone.utc,
        )

        route, reasons = (
            coordinator.select_update_route(
                latest,
                latest - timedelta(hours=1),
                (
                    latest
                    + timedelta(days=4)
                    + timedelta(milliseconds=1)
                ),
            )
        )

        self.assertEqual(
            route,
            coordinator.HISTORICAL_CATCHUP_ROUTE,
        )

        self.assertTrue(reasons)

    def test_recent_coverage_gap_requires_catchup(self):
        """A recent export starting after history leaves a gap."""

        latest = datetime(
            2026,
            8,
            1,
            tzinfo=timezone.utc,
        )

        route, reasons = (
            coordinator.select_update_route(
                latest,
                latest + timedelta(seconds=1),
                latest + timedelta(days=2),
            )
        )

        self.assertEqual(
            route,
            coordinator.HISTORICAL_CATCHUP_ROUTE,
        )

        self.assertTrue(reasons)

    def test_recent_route_metadata_is_read_from_export(self):
        """Valid browser metadata should produce aware UTC times."""

        with tempfile.TemporaryDirectory() as directory:
            path = (
                Path(directory)
                / "recent-preview.json"
            )

            path.write_text(
                """
{
  "retrieved_at_utc": "2026-08-05T12:00:10.000Z",
  "estimated_first_timestamp_utc": "2026-08-01T12:00:00.000Z",
  "estimated_last_timestamp_utc": "2026-08-05T12:00:00.000Z",
  "price_count": 34561,
  "instrument": "USD-XAU",
  "source": "GoldPrice.org"
}
""".strip(),
                encoding="utf-8",
            )

            metadata = (
                coordinator.read_recent_route_metadata(
                    path
                )
            )

        self.assertEqual(
            metadata["first_timestamp"],
            datetime(
                2026,
                8,
                1,
                12,
                0,
                tzinfo=timezone.utc,
            ),
        )

        self.assertEqual(
            metadata["retrieved_timestamp"],
            datetime(
                2026,
                8,
                5,
                12,
                0,
                10,
                tzinfo=timezone.utc,
            ),
        )

        self.assertEqual(
            metadata["price_count"],
            34561,
        )

    def test_recent_route_metadata_rejects_missing_field(self):
        """Missing route metadata must stop before updating."""

        with tempfile.TemporaryDirectory() as directory:
            path = (
                Path(directory)
                / "recent-preview.json"
            )

            path.write_text(
                """
{
  "retrieved_at_utc": "2026-08-05T12:00:10.000Z",
  "estimated_last_timestamp_utc": "2026-08-05T12:00:00.000Z",
  "price_count": 34561,
  "instrument": "USD-XAU",
  "source": "GoldPrice.org"
}
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                coordinator.read_recent_route_metadata(
                    path
                )

    def test_recent_route_metadata_rejects_naive_time(self):
        """Route timestamps must include a timezone."""

        with tempfile.TemporaryDirectory() as directory:
            path = (
                Path(directory)
                / "recent-preview.json"
            )

            path.write_text(
                """
{
  "retrieved_at_utc": "2026-08-05T12:00:10",
  "estimated_first_timestamp_utc": "2026-08-01T12:00:00.000Z",
  "estimated_last_timestamp_utc": "2026-08-05T12:00:00.000Z",
  "price_count": 34561,
  "instrument": "USD-XAU",
  "source": "GoldPrice.org"
}
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                coordinator.read_recent_route_metadata(
                    path
                )

    def test_latest_permanent_timestamp_is_read(self):
        """The final chronological CSV row defines current coverage."""

        with tempfile.TemporaryDirectory() as directory:
            path = (
                Path(directory)
                / "international.csv"
            )

            path.write_text(
                (
                    "timestamp_utc,date,"
                    "price_usd_per_troy_ounce,"
                    "price_usd_per_gram,source\n"
                    "2026-08-01T04:00:00.000Z,"
                    "2026-08-01,4000.0000,"
                    "128.603222,GoldPrice.org\n"
                    "2026-08-02T04:00:00.000Z,"
                    "2026-08-02,4010.0000,"
                    "128.924730,GoldPrice.org\n"
                ),
                encoding="utf-8",
            )

            actual = (
                coordinator
                .read_latest_permanent_timestamp(
                    path
                )
            )

        self.assertEqual(
            actual,
            datetime(
                2026,
                8,
                2,
                4,
                0,
                tzinfo=timezone.utc,
            ),
        )

    def test_preview_uses_read_only_commands(self):
        """Preview mode must not pass --apply to either updater."""

        recent_input = Path(
            "recent-preview.json"
        )

        with patch.object(
            coordinator,
            "run_python",
        ) as run_python:
            coordinator.run_preview(
                recent_input
            )

        self.assertEqual(
            run_python.call_args_list,
            [
                call(
                    "Preview international gold update",
                    [
                        "scripts/"
                        "update_international_gold.py",
                        "--recent-input",
                        str(recent_input),
                    ],
                ),
                call(
                    "Preview USD/INR update",
                    [
                        "scripts/"
                        "update_usd_inr.py",
                        "--fetch",
                    ],
                ),
            ],
        )

    def test_apply_runs_commands_in_required_order(self):
        """Apply mode must update sources before regenerating the reference."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            paths = [
                root / "international.csv",
                root / "usd-inr.csv",
                root / "reference.csv",
            ]

            for path in paths:
                path.write_text(
                    f"original:{path.name}",
                    encoding="utf-8",
                )

            recent_input = (
                root / "recent-preview.json"
            )

            recent_input.write_text(
                "{}",
                encoding="utf-8",
            )

            with (
                patch.object(
                    coordinator,
                    "INTERNATIONAL_GOLD_PATH",
                    paths[0],
                ),
                patch.object(
                    coordinator,
                    "USD_INR_PATH",
                    paths[1],
                ),
                patch.object(
                    coordinator,
                    "REFERENCE_PATH",
                    paths[2],
                ),
                patch.object(
                    coordinator,
                    "PERMANENT_PATHS",
                    paths,
                ),
                patch.object(
                    coordinator,
                    "run_python",
                ) as run_python,
            ):
                coordinator.run_apply(
                    recent_input
                )

            labels = [
                current_call.args[0]
                for current_call
                in run_python.call_args_list
            ]

            self.assertEqual(
                labels,
                [
                    "Apply international gold update",
                    "Apply USD/INR update",
                    (
                        "Regenerate international "
                        "22K INR reference"
                    ),
                    (
                        "Validate international "
                        "gold history"
                    ),
                    "Validate USD/INR history",
                    (
                        "Validate international "
                        "22K INR reference"
                    ),
                    (
                        "Run complete project "
                        "test suite"
                    ),
                ],
            )

    def test_failure_restores_all_permanent_files(self):
        """A later failure must restore every dataset byte for byte."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            international_path = (
                root / "international.csv"
            )

            usd_inr_path = (
                root / "usd-inr.csv"
            )

            reference_path = (
                root / "reference.csv"
            )

            paths = [
                international_path,
                usd_inr_path,
                reference_path,
            ]

            original_content = {
                international_path:
                    b"original international\n",
                usd_inr_path:
                    b"original fx\n",
                reference_path:
                    b"original reference\n",
            }

            for path, content in (
                original_content.items()
            ):
                path.write_bytes(content)

            recent_input = (
                root / "recent-preview.json"
            )

            recent_input.write_text(
                "{}",
                encoding="utf-8",
            )

            def simulated_command(
                label: str,
                arguments: list[str],
            ) -> None:
                if (
                    label ==
                    "Apply international gold update"
                ):
                    international_path.write_bytes(
                        b"changed international\n"
                    )

                    return

                if (
                    label ==
                    "Apply USD/INR update"
                ):
                    usd_inr_path.write_bytes(
                        b"changed fx\n"
                    )

                    raise subprocess.CalledProcessError(
                        1,
                        arguments,
                    )

                # Validator calls after restoration
                # are allowed to complete normally.
                return

            with (
                patch.object(
                    coordinator,
                    "INTERNATIONAL_GOLD_PATH",
                    international_path,
                ),
                patch.object(
                    coordinator,
                    "USD_INR_PATH",
                    usd_inr_path,
                ),
                patch.object(
                    coordinator,
                    "REFERENCE_PATH",
                    reference_path,
                ),
                patch.object(
                    coordinator,
                    "PERMANENT_PATHS",
                    paths,
                ),
                patch.object(
                    coordinator,
                    "run_python",
                    side_effect=simulated_command,
                ),
            ):
                with self.assertRaises(
                    subprocess.CalledProcessError
                ):
                    coordinator.run_apply(
                        recent_input
                    )

            for path, expected_content in (
                original_content.items()
            ):
                self.assertEqual(
                    path.read_bytes(),
                    expected_content,
                )

    def test_failure_stops_before_generation(self):
        """A failed source update must prevent later workflow steps."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            paths = [
                root / "international.csv",
                root / "usd-inr.csv",
                root / "reference.csv",
            ]

            for path in paths:
                path.write_bytes(
                    b"original\n"
                )

            recent_input = (
                root / "recent-preview.json"
            )

            recent_input.write_text(
                "{}",
                encoding="utf-8",
            )

            labels: list[str] = []

            def simulated_command(
                label: str,
                arguments: list[str],
            ) -> None:
                labels.append(label)

                if (
                    label ==
                    "Apply international gold update"
                ):
                    raise subprocess.CalledProcessError(
                        1,
                        arguments,
                    )

                return

            with (
                patch.object(
                    coordinator,
                    "INTERNATIONAL_GOLD_PATH",
                    paths[0],
                ),
                patch.object(
                    coordinator,
                    "USD_INR_PATH",
                    paths[1],
                ),
                patch.object(
                    coordinator,
                    "REFERENCE_PATH",
                    paths[2],
                ),
                patch.object(
                    coordinator,
                    "PERMANENT_PATHS",
                    paths,
                ),
                patch.object(
                    coordinator,
                    "run_python",
                    side_effect=simulated_command,
                ),
            ):
                with self.assertRaises(
                    subprocess.CalledProcessError
                ):
                    coordinator.run_apply(
                        recent_input
                    )

            self.assertNotIn(
                (
                    "Regenerate international "
                    "22K INR reference"
                ),
                labels,
            )

    def test_missing_historical_input_stops_workflow(self):
        """Catch-up must stop before any updater is started."""

        recent_input = Path(
            "recent-preview.json"
        )

        recent_metadata = {
            "first_timestamp": datetime(
                2026,
                8,
                10,
                tzinfo=timezone.utc,
            ),
            "last_timestamp": datetime(
                2026,
                8,
                14,
                tzinfo=timezone.utc,
            ),
            "retrieved_timestamp": datetime(
                2026,
                8,
                14,
                0,
                0,
                10,
                tzinfo=timezone.utc,
            ),
            "price_count": 100,
        }

        arguments = argparse.Namespace(
            recent_input=recent_input,
            historical_input=None,
            apply=False,
        )

        with (
            patch.object(
                coordinator,
                "parse_args",
                return_value=arguments,
            ),
            patch.object(
                Path,
                "exists",
                return_value=True,
            ),
            patch.object(
                coordinator,
                "read_latest_permanent_timestamp",
                return_value=datetime(
                    2026,
                    8,
                    1,
                    tzinfo=timezone.utc,
                ),
            ),
            patch.object(
                coordinator,
                "read_recent_route_metadata",
                return_value=recent_metadata,
            ),
            patch.object(
                coordinator,
                "run_preview",
            ) as run_preview,
            patch.object(
                coordinator,
                "run_apply",
            ) as run_apply,
        ):
            result = coordinator.main()

        self.assertEqual(
            result,
            2,
        )

        run_preview.assert_not_called()
        run_apply.assert_not_called()

    def test_main_rejects_missing_recent_input(self):
        """A missing browser export must stop before any workflow runs."""

        missing_path = Path(
            "missing-recent-export.json"
        )

        arguments = argparse.Namespace(
            recent_input=missing_path,
            historical_input=None,
            apply=False,
        )

        with (
            patch.object(
                coordinator,
                "parse_args",
                return_value=arguments,
            ),
            patch.object(
                coordinator,
                "run_preview",
            ) as run_preview,
        ):
            result = coordinator.main()

        self.assertEqual(
            result,
            1,
        )

        run_preview.assert_not_called()

    def test_preview_passes_historical_input_to_updater(
        self
    ):
        """Catch-up preview must pass both browser exports."""

        recent_input = Path(
            "recent-preview.json"
        )

        historical_input = Path(
            "historical-preview.csv"
        )

        with patch.object(
            coordinator,
            "run_python",
        ) as run_python:
            coordinator.run_preview(
                recent_input,
                historical_input=historical_input,
            )

        self.assertEqual(
            run_python.call_args_list[0],
            call(
                "Preview international gold update",
                [
                    "scripts/"
                    "update_international_gold.py",
                    "--recent-input",
                    str(recent_input),
                    "--historical-input",
                    str(historical_input),
                ],
            ),
        )

    def test_apply_passes_historical_input_to_updater(
        self
    ):
        """Catch-up apply must pass both inputs and --apply."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            paths = [
                root / "international.csv",
                root / "usd-inr.csv",
                root / "reference.csv",
            ]

            for path in paths:
                path.write_bytes(
                    b"original\n"
                )

            recent_input = (
                root / "recent-preview.json"
            )

            historical_input = (
                root / "historical-preview.csv"
            )

            recent_input.write_text(
                "{}",
                encoding="utf-8",
            )

            historical_input.write_text(
                "fixture",
                encoding="utf-8",
            )

            with (
                patch.object(
                    coordinator,
                    "INTERNATIONAL_GOLD_PATH",
                    paths[0],
                ),
                patch.object(
                    coordinator,
                    "USD_INR_PATH",
                    paths[1],
                ),
                patch.object(
                    coordinator,
                    "REFERENCE_PATH",
                    paths[2],
                ),
                patch.object(
                    coordinator,
                    "PERMANENT_PATHS",
                    paths,
                ),
                patch.object(
                    coordinator,
                    "run_python",
                ) as run_python,
            ):
                coordinator.run_apply(
                    recent_input,
                    historical_input=historical_input,
                )

        self.assertEqual(
            run_python.call_args_list[0],
            call(
                "Apply international gold update",
                [
                    "scripts/"
                    "update_international_gold.py",
                    "--recent-input",
                    str(recent_input),
                    "--historical-input",
                    str(historical_input),
                    "--apply",
                ],
            ),
        )


if __name__ == "__main__":
    unittest.main()