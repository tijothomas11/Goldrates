"""Tests for the international-reference workflow coordinator."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import unittest
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

    def test_main_rejects_missing_recent_input(self):
        """A missing browser export must stop before any workflow runs."""

        missing_path = Path(
            "missing-recent-export.json"
        )

        arguments = argparse.Namespace(
            recent_input=missing_path,
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


if __name__ == "__main__":
    unittest.main()