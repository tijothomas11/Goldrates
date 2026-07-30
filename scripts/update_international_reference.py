"""Coordinate the international gold and INR-reference update workflow.

The coordinator runs the existing update, generation, validation, and test
commands in the required order. It does not duplicate their parsing, merge,
conflict-detection, or file-validation logic.

Preview mode is read-only. Before apply mode begins, the coordinator saves
temporary copies of all three permanent datasets. If any later step fails,
all three files are restored to their original state.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

INTERNATIONAL_GOLD_PATH = (
    PROJECT_ROOT
    / "data"
    / "international_gold_spot.csv"
)

USD_INR_PATH = (
    PROJECT_ROOT
    / "data"
    / "usd_inr_history.csv"
)

REFERENCE_PATH = (
    PROJECT_ROOT
    / "data"
    / "international_gold_22k_inr.csv"
)

PERMANENT_PATHS = [
    INTERNATIONAL_GOLD_PATH,
    USD_INR_PATH,
    REFERENCE_PATH,
]


def parse_args() -> argparse.Namespace:
    """Read the browser-export input and optional apply flag."""

    parser = argparse.ArgumentParser(
        description=(
            "Preview or apply the complete international "
            "gold and INR-reference update workflow."
        )
    )

    parser.add_argument(
        "--recent-input",
        type=Path,
        required=True,
        help=(
            "Path to the recent GoldPrice.org JSON "
            "export downloaded from the authorized "
            "browser page."
        ),
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Apply validated updates. Without this "
            "option, every update step remains "
            "read-only."
        ),
    )

    return parser.parse_args()


def run_python(
    label: str,
    arguments: list[str],
) -> None:
    """Run one Python command and stop if it fails."""

    print()
    print(label)
    print("-" * len(label))

    command = [
        sys.executable,
        *arguments,
    ]

    print(
        "Running:",
        " ".join(command),
        flush=True,
    )

    subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=True,
    )


def create_workflow_snapshots(
    backup_directory: Path,
) -> dict[Path, Path | None]:
    """Save temporary copies of all permanent datasets.

    The individual updater scripts already create their own permanent
    backups. These additional snapshots protect the complete multi-step
    workflow so every dataset can be restored together if a later command
    fails.
    """

    snapshots: dict[
        Path,
        Path | None,
    ] = {}

    for index, permanent_path in enumerate(
        PERMANENT_PATHS
    ):
        if not permanent_path.exists():
            snapshots[
                permanent_path
            ] = None
            continue

        snapshot_path = (
            backup_directory
            / f"{index}_{permanent_path.name}"
        )

        shutil.copy2(
            permanent_path,
            snapshot_path,
        )

        snapshots[
            permanent_path
        ] = snapshot_path

    return snapshots


def restore_workflow_snapshots(
    snapshots: dict[Path, Path | None],
) -> None:
    """Restore every permanent dataset to its original state."""

    print()
    print("Restoring permanent datasets")
    print("----------------------------")

    for permanent_path, snapshot_path in (
        snapshots.items()
    ):
        if snapshot_path is None:
            permanent_path.unlink(
                missing_ok=True
            )

            print(
                "Removed newly created file:",
                permanent_path,
            )

            continue

        permanent_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        shutil.copy2(
            snapshot_path,
            permanent_path,
        )

        print(
            "Restored:",
            permanent_path,
        )


def verify_workflow_restore(
    snapshots: dict[Path, Path | None],
) -> None:
    """Require every restored file to match its snapshot."""

    for permanent_path, snapshot_path in (
        snapshots.items()
    ):
        if snapshot_path is None:
            if permanent_path.exists():
                raise RuntimeError(
                    "Rollback left an unexpected file: "
                    f"{permanent_path}"
                )

            continue

        if not permanent_path.exists():
            raise RuntimeError(
                "Rollback did not restore: "
                f"{permanent_path}"
            )

        if (
            permanent_path.read_bytes() !=
            snapshot_path.read_bytes()
        ):
            raise RuntimeError(
                "Restored file does not match "
                f"its snapshot: {permanent_path}"
            )


def validate_permanent_datasets() -> None:
    """Run the existing validators for all permanent datasets."""

    run_python(
        "Validate international gold history",
        [
            "scripts/"
            "validate_international_gold.py",
            str(INTERNATIONAL_GOLD_PATH),
        ],
    )

    run_python(
        "Validate USD/INR history",
        [
            "scripts/validate_usd_inr.py",
            str(USD_INR_PATH),
        ],
    )

    run_python(
        "Validate international 22K INR reference",
        [
            "scripts/"
            "validate_international_22k_reference.py",
            str(REFERENCE_PATH),
        ],
    )


def run_preview(
    recent_input: Path,
) -> None:
    """Preview both source updates without modifying permanent files."""

    run_python(
        "Preview international gold update",
        [
            "scripts/update_international_gold.py",
            "--recent-input",
            str(recent_input),
        ],
    )

    run_python(
        "Preview USD/INR update",
        [
            "scripts/update_usd_inr.py",
            "--fetch",
        ],
    )

    print()
    print("Preview summary")
    print("---------------")
    print(
        "No permanent file was changed."
    )
    print(
        "The derived 22K INR reference will be "
        "regenerated only during apply mode, after "
        "both source updates succeed."
    )


def run_apply(
    recent_input: Path,
) -> None:
    """Apply the complete workflow with shared rollback protection."""

    with tempfile.TemporaryDirectory(
        prefix="international_reference_workflow_"
    ) as temporary_directory:
        snapshots = create_workflow_snapshots(
            Path(temporary_directory)
        )

        try:
            run_python(
                "Apply international gold update",
                [
                    "scripts/"
                    "update_international_gold.py",
                    "--recent-input",
                    str(recent_input),
                    "--apply",
                ],
            )

            run_python(
                "Apply USD/INR update",
                [
                    "scripts/update_usd_inr.py",
                    "--fetch",
                    "--apply",
                ],
            )

            run_python(
                "Regenerate international 22K INR reference",
                [
                    "scripts/"
                    "generate_international_22k_reference.py",
                    "--apply",
                ],
            )

            validate_permanent_datasets()

            run_python(
                "Run complete project test suite",
                [
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-v",
                ],
            )

        except BaseException as original_error:
            restore_workflow_snapshots(
                snapshots
            )

            verify_workflow_restore(
                snapshots
            )

            print()
            print(
                "The original permanent datasets "
                "were restored byte for byte."
            )

            try:
                validate_permanent_datasets()
            except Exception as validation_error:
                raise RuntimeError(
                    "The workflow failed and the files "
                    "were restored, but restored-file "
                    "validation also failed."
                ) from validation_error

            print(
                "The restored permanent datasets "
                "passed validation."
            )

            raise original_error

    print()
    print("Update completed")
    print("----------------")
    print(
        "All update, generation, validation, "
        "and test steps passed."
    )
    print()
    print("Review these permanent files:")
    print(
        "  data/international_gold_spot.csv"
    )
    print(
        "  data/usd_inr_history.csv"
    )
    print(
        "  data/international_gold_22k_inr.csv"
    )


def show_changed_files() -> None:
    """Display repository changes without staging anything."""

    print()
    print("Changed files")
    print("-------------")

    try:
        subprocess.run(
            [
                "git",
                "status",
                "--short",
            ],
            cwd=PROJECT_ROOT,
            check=True,
        )
    except subprocess.CalledProcessError:
        print(
            "Git status could not be displayed."
        )


def main() -> int:
    """Run the complete preview or apply workflow."""

    args = parse_args()

    recent_input = (
        args.recent_input.resolve()
    )

    if not recent_input.exists():
        print(
            "Recent browser export does not exist:",
            recent_input,
        )

        return 1

    try:
        if args.apply:
            run_apply(
                recent_input
            )
        else:
            run_preview(
                recent_input
            )

    except subprocess.CalledProcessError as exc:
        print()
        print(
            "Workflow stopped because one step "
            "failed."
        )
        print(
            "Failed command:",
            " ".join(
                str(part)
                for part in exc.cmd
            ),
        )
        print(
            "Exit code:",
            exc.returncode,
        )
        print(
            "Review the failed step before "
            "running apply mode again."
        )

        show_changed_files()

        return exc.returncode or 1

    except Exception as exc:
        print()
        print(
            "International reference workflow "
            "failed:",
            exc,
        )

        show_changed_files()

        return 1

    show_changed_files()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())