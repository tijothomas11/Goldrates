"""Safely apply new observations to permanent gold-rate history.

The script:

- fetches current gram and pavan observations;
- performs the immutable-history comparison again;
- aborts if an existing price has changed;
- creates a timestamped backup;
- appends only new date/session observations;
- writes the permanent CSV atomically;
- runs the permanent-history validator;
- restores the backup if validation fails.

Usage:
    python scripts/apply_history_update.py --apply
"""

import argparse
import csv
import datetime as dt
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
BACKUP_DIR = DATA_DIR / "backups"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(
        0,
        str(Path(__file__).resolve().parent),
    )


from history_update import (  # noqa: E402
    HistoryConflictError,
    merge_history_rows,
)

from backfill_history import (  # noqa: E402
    OUTPUT_FIELDS,
)

from preview_history_update import (  # noqa: E402
    CONFLICT_PATH,
    PERMANENT_HISTORY_PATH,
    PREVIEW_PATH,
    fetch_current_observations,
    load_permanent_history,
    write_conflicts,
    write_csv,
)


def parse_args():
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Safely append new observations to "
            "permanent gold-rate history."
        )
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Apply the update. Without this option, "
            "the permanent history is not modified."
        ),
    )

    return parser.parse_args()


def create_backup():
    """Create a timestamped permanent-history backup."""

    BACKUP_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = dt.datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    backup_path = BACKUP_DIR / (
        "gold_rates_history."
        f"before_daily_update_{timestamp}.csv"
    )

    shutil.copy2(
        PERMANENT_HISTORY_PATH,
        backup_path,
    )

    return backup_path


def write_permanent_history(rows):
    """Write permanent history through a temporary file."""

    temporary_path = (
        PERMANENT_HISTORY_PATH.with_suffix(
            ".csv.tmp"
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

            file.flush()

        temporary_path.replace(
            PERMANENT_HISTORY_PATH
        )

    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()

        raise


def restore_backup(backup_path):
    """Restore permanent history from a backup."""

    shutil.copy2(
        backup_path,
        PERMANENT_HISTORY_PATH,
    )


def run_validator():
    """Run permanent-history validation."""

    command = [
        sys.executable,
        str(
            PROJECT_ROOT
            / "scripts"
            / "validate_backfill.py"
        ),
    ]

    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
    ).returncode


def regenerate_outputs():
    """Regenerate daily CSV, Excel, SVG and live snapshot."""

    command = [
        sys.executable,
        str(PROJECT_ROOT / "goldrate_tracker.py"),
    ]

    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
    ).returncode


def display_new_observations(
    existing_rows,
    observed_rows,
):
    """Print observations not already in permanent history."""

    existing_keys = {
        (
            row["date"].strip(),
            row["session"].strip(),
        )
        for row in existing_rows
    }

    new_rows = []

    for row in observed_rows:
        key = (
            row["date"].strip(),
            row["session"].strip(),
        )

        if key not in existing_keys:
            new_rows.append(row)

    for row in new_rows:
        print(
            f"{row['date']} | "
            f"{row['session'] or 'Standard'} | "
            f"gram="
            f"{row['normalized_price_22k_1g']} | "
            f"pavan="
            f"{row['price_22k_8g_published']}"
        )

    return new_rows


def main():
    args = parse_args()

    print("Checking permanent-history update...")
    print("")

    try:
        existing_rows = load_permanent_history()

        (
            gram_records,
            pavan_records,
            observed_rows,
        ) = fetch_current_observations()

    except Exception as exc:
        print(f"Update check failed: {exc}")
        return 1

    write_csv(
        PREVIEW_PATH,
        OUTPUT_FIELDS,
        observed_rows,
    )

    print("")
    print("Live-page observations")
    print("----------------------")
    print(f"Gram records: {len(gram_records)}")
    print(f"Pavan records: {len(pavan_records)}")
    print(
        f"Combined records: {len(observed_rows)}"
    )

    try:
        result = merge_history_rows(
            existing_rows,
            observed_rows,
        )

    except HistoryConflictError as exc:
        write_conflicts(exc.conflicts)

        print("")
        print("UPDATE ABORTED: CONFLICT DETECTED")
        print("---------------------------------")
        print(
            f"Conflicting fields: "
            f"{len(exc.conflicts)}"
        )

        for conflict in exc.conflicts[:20]:
            print(
                f"{conflict.date} | "
                f"{conflict.session or 'Standard'} | "
                f"{conflict.field} | "
                f"stored={conflict.stored_value} | "
                f"observed={conflict.observed_value}"
            )

        remaining = len(exc.conflicts) - 20

        if remaining > 0:
            print(
                f"... and {remaining} more conflicts."
            )

        print("")
        print(
            "Permanent history was not modified."
        )
        print(
            f"Conflict report: {CONFLICT_PATH}"
        )

        return 2

    except Exception as exc:
        print(f"Comparison failed: {exc}")
        print(
            "Permanent history was not modified."
        )
        return 1

    print("")
    print("Comparison result")
    print("-----------------")
    print(
        f"Permanent records: {len(existing_rows)}"
    )
    print(
        f"Existing unchanged: "
        f"{result.unchanged_count}"
    )
    print(
        f"New observations: {result.added_count}"
    )
    print("Conflicts: 0")
    print(
        f"Records after merge: "
        f"{len(result.rows)}"
    )

    if result.added_count:
        print("")
        print("New observations")
        print("----------------")

        display_new_observations(
            existing_rows,
            observed_rows,
        )

    if not args.apply:
        print("")
        print(
            "Preview only. Permanent history "
            "was not modified."
        )
        print(
            "Run with --apply to write the update:"
        )
        print(
            "python "
            "scripts/apply_history_update.py "
            "--apply"
        )
        return 0

    if result.added_count == 0:
        print("")
        print(
            "Permanent history is already up to date."
        )
        print(
            "No permanent-history write was needed."
        )

        if CONFLICT_PATH.exists():
            CONFLICT_PATH.unlink()

        return 0

    backup_path = create_backup()

    print("")
    print("Backup created:")
    print(f"  {backup_path}")

    try:
        write_permanent_history(
            result.rows
        )

    except Exception as exc:
        print("")
        print(
            f"Permanent-history write failed: {exc}"
        )
        restore_backup(backup_path)
        print(
            "Previous permanent history was restored."
        )
        return 1

    print("")
    print("Running permanent-history validation...")

    validation_result = run_validator()

    if validation_result != 0:
        print("")
        print(
            "Validation failed after update."
        )
        restore_backup(backup_path)
        print(
            "Previous permanent history was restored."
        )
        return 1

    print("")
    print(
        "Permanent history updated successfully."
    )
    print(
        f"Previous records: {len(existing_rows)}"
    )
    print(
        f"Current records: {len(result.rows)}"
    )
    print(
        f"New observations: {result.added_count}"
    )

    if CONFLICT_PATH.exists():
        CONFLICT_PATH.unlink()

    print("")
    print("Regenerating chart outputs...")

    output_result = regenerate_outputs()

    if output_result != 0:
        print("")
        print(
            "Warning: permanent history was updated "
            "and validated, but generated outputs "
            "could not be refreshed."
        )
        print(
            "Run python goldrate_tracker.py "
            "manually to retry."
        )
        return 3

    print("")
    print("Update workflow completed.")
    print(
        f"Permanent history: "
        f"{PERMANENT_HISTORY_PATH}"
    )
    print(
        f"Backup: {backup_path}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())