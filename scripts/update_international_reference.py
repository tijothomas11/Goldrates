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
import csv
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
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

RECENT_ONLY_MAX_AGE = timedelta(
    days=4
)

RECENT_ONLY_ROUTE = "RECENT ONLY"

HISTORICAL_CATCHUP_ROUTE = (
    "HISTORICAL CATCH-UP"
)

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
        "--historical-input",
        type=Path,
        help=(
            "Optional historical GoldPrice.org CSV "
            "export. This is required when the recent "
            "export cannot cover the missing period."
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

def parse_utc_timestamp(
    value: object,
    field_name: str,
) -> datetime:
    """Parse a timezone-aware timestamp and normalize it to UTC."""

    if not isinstance(value, str):
        raise ValueError(
            f"{field_name} must be a text timestamp."
        )

    try:
        timestamp = datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00",
            )
        )
    except ValueError as exc:
        raise ValueError(
            f"{field_name} is not a valid ISO timestamp: "
            f"{value!r}"
        ) from exc

    if (
        timestamp.tzinfo is None or
        timestamp.utcoffset() is None
    ):
        raise ValueError(
            f"{field_name} must include a timezone."
        )

    return timestamp.astimezone(
        timezone.utc
    )


def read_latest_permanent_timestamp(
    path: Path,
) -> datetime:
    """Read the final chronological observation from permanent history."""

    if not path.exists():
        raise FileNotFoundError(
            "Permanent international history "
            f"does not exist: {path}"
        )

    latest_timestamp: datetime | None = None

    with path.open(
        "r",
        encoding="utf-8",
        newline="",
    ) as file:
        reader = csv.DictReader(file)

        if (
            reader.fieldnames is None or
            "timestamp_utc" not in reader.fieldnames
        ):
            raise ValueError(
                "Permanent international history is "
                "missing the timestamp_utc column."
            )

        for row_number, row in enumerate(
            reader,
            start=2,
        ):
            timestamp_text = row.get(
                "timestamp_utc",
                "",
            )

            try:
                timestamp = parse_utc_timestamp(
                    timestamp_text,
                    (
                        "Permanent row "
                        f"{row_number} timestamp_utc"
                    ),
                )
            except ValueError as exc:
                raise ValueError(
                    "Permanent international history "
                    f"contains an invalid row: {exc}"
                ) from exc

            if (
                latest_timestamp is not None and
                timestamp <= latest_timestamp
            ):
                raise ValueError(
                    "Permanent international history "
                    "must be strictly chronological."
                )

            latest_timestamp = timestamp

    if latest_timestamp is None:
        raise ValueError(
            "Permanent international history "
            "contains no observations."
        )

    return latest_timestamp


def read_recent_route_metadata(
    path: Path,
) -> dict[str, object]:
    """Read and validate metadata used to choose the update route."""

    if not path.exists():
        raise FileNotFoundError(
            f"Recent browser export does not exist: {path}"
        )

    try:
        payload = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Recent browser export is not valid JSON."
        ) from exc

    if not isinstance(payload, dict):
        raise ValueError(
            "Recent browser export must contain "
            "one JSON object."
        )

    if payload.get("instrument") != "USD-XAU":
        raise ValueError(
            "Recent browser export has an "
            "unexpected instrument."
        )

    if payload.get("source") != "GoldPrice.org":
        raise ValueError(
            "Recent browser export has an "
            "unexpected source."
        )

    price_count = payload.get(
        "price_count"
    )

    if (
        not isinstance(price_count, int) or
        isinstance(price_count, bool) or
        price_count <= 0
    ):
        raise ValueError(
            "Recent browser export must contain "
            "a positive integer price_count."
        )

    first_timestamp = parse_utc_timestamp(
        payload.get(
            "estimated_first_timestamp_utc"
        ),
        "estimated_first_timestamp_utc",
    )

    last_timestamp = parse_utc_timestamp(
        payload.get(
            "estimated_last_timestamp_utc"
        ),
        "estimated_last_timestamp_utc",
    )

    retrieved_timestamp = parse_utc_timestamp(
        payload.get(
            "retrieved_at_utc"
        ),
        "retrieved_at_utc",
    )

    if first_timestamp > last_timestamp:
        raise ValueError(
            "The recent export begins after "
            "its final observation."
        )

    if last_timestamp >= retrieved_timestamp:
        raise ValueError(
            "The recent export's final observation "
            "must be earlier than its retrieval time."
        )

    return {
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp,
        "retrieved_timestamp":
            retrieved_timestamp,
        "price_count": price_count,
    }


def select_update_route(
    latest_permanent_timestamp: datetime,
    recent_first_timestamp: datetime,
    recent_retrieved_timestamp: datetime,
) -> tuple[str, list[str]]:
    """Choose recent-only or historical catch-up processing.

    Exactly four days remains eligible for recent-only processing.
    Historical catch-up is required when permanent data is older than
    four complete days or when the recent export does not overlap the
    latest permanent observation.
    """

    reasons: list[str] = []

    permanent_age = (
        recent_retrieved_timestamp -
        latest_permanent_timestamp
    )

    if permanent_age > RECENT_ONLY_MAX_AGE:
        reasons.append(
            "The latest permanent observation is "
            "more than four days older than the "
            "recent export retrieval time."
        )

    if (
        recent_first_timestamp >
        latest_permanent_timestamp
    ):
        reasons.append(
            "The recent export begins after the "
            "latest permanent observation and "
            "leaves an uncovered gap."
        )

    if reasons:
        return (
            HISTORICAL_CATCHUP_ROUTE,
            reasons,
        )

    return (
        RECENT_ONLY_ROUTE,
        reasons,
    )


def format_utc_timestamp(
    value: datetime,
) -> str:
    """Format an aware UTC timestamp for route reports."""

    return (
        value
        .astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def print_update_route(
    route: str,
    reasons: list[str],
    latest_permanent_timestamp: datetime,
    recent_metadata: dict[str, object],
) -> None:
    """Explain the selected route before starting an updater."""

    recent_first_timestamp = recent_metadata[
        "first_timestamp"
    ]

    recent_retrieved_timestamp = recent_metadata[
        "retrieved_timestamp"
    ]

    if not isinstance(
        recent_first_timestamp,
        datetime,
    ):
        raise TypeError(
            "Recent first timestamp must be a datetime."
        )

    if not isinstance(
        recent_retrieved_timestamp,
        datetime,
    ):
        raise TypeError(
            "Recent retrieval timestamp must be a datetime."
        )

    print()
    print("Update route")
    print("------------")
    print(
        "Route:",
        route,
    )
    print(
        "Latest permanent observation:",
        format_utc_timestamp(
            latest_permanent_timestamp
        ),
    )
    print(
        "Recent export begins:",
        format_utc_timestamp(
            recent_first_timestamp
        ),
    )
    print(
        "Recent export retrieved:",
        format_utc_timestamp(
            recent_retrieved_timestamp
        ),
    )

    if route == RECENT_ONLY_ROUTE:
        print(
            "The recent export overlaps permanent "
            "history and is within the four-day limit."
        )
        print(
            "No historical export is required."
        )

        return

    for reason in reasons:
        print(
            "Reason:",
            reason,
        )

    print(
        "The recent export cannot cover the "
        "complete missing period."
    )

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
    """Select the update route, then run preview or apply."""

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

    historical_input = (
        args.historical_input.resolve()
        if args.historical_input is not None
        else None
    )

    if (
        historical_input is not None and
        not historical_input.exists()
    ):
        print(
            "Historical browser export "
            "does not exist:",
            historical_input,
        )

        return 1

    try:
        latest_permanent_timestamp = (
            read_latest_permanent_timestamp(
                INTERNATIONAL_GOLD_PATH
            )
        )

        recent_metadata = (
            read_recent_route_metadata(
                recent_input
            )
        )

        recent_first_timestamp = (
            recent_metadata[
                "first_timestamp"
            ]
        )

        recent_retrieved_timestamp = (
            recent_metadata[
                "retrieved_timestamp"
            ]
        )

        if not isinstance(
            recent_first_timestamp,
            datetime,
        ):
            raise TypeError(
                "Recent first timestamp must "
                "be a datetime."
            )

        if not isinstance(
            recent_retrieved_timestamp,
            datetime,
        ):
            raise TypeError(
                "Recent retrieval timestamp must "
                "be a datetime."
            )

        route, route_reasons = (
            select_update_route(
                latest_permanent_timestamp,
                recent_first_timestamp,
                recent_retrieved_timestamp,
            )
        )

        print_update_route(
            route,
            route_reasons,
            latest_permanent_timestamp,
            recent_metadata,
        )

        if route == HISTORICAL_CATCHUP_ROUTE:
            if historical_input is None:
                print()
                print(
                    "Historical catch-up input is "
                    "required."
                )
                print("Expected file:")
                print(
                    "  international_gold_"
                    "historical_preview.csv"
                )
                print()
                print(
                    "No permanent file was changed."
                )

                return 2

            print()
            print(
                "Historical input was supplied:"
            )
            print(
                " ",
                historical_input,
            )
            print(
                "Historical parsing is not connected "
                "in this checkpoint."
            )
            print(
                "No permanent file was changed."
            )

            # Applying now would continue without using
            # the required catch-up data. Stop until the
            # historical parser is connected.
            return 2

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