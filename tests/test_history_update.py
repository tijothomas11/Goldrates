"""Tests for immutable permanent-history merging."""

import unittest

from history_update import (
    HistoryConflictError,
    merge_history_rows,
)


def make_row(
    date,
    session,
    gram,
    pavan,
):
    """Create one valid test observation."""

    gram_from_pavan = str(
        int(pavan) / 8
    ).rstrip("0").rstrip(".")

    return {
        "date": date,
        "session": session,
        "price_22k_1g_published": str(gram),
        "price_22k_8g_published": str(pavan),
        "price_22k_1g_from_pavan": gram_from_pavan,
        "difference_22k": "0",
        "normalized_price_22k_1g": str(gram),
        "gram_source_url": "https://example.com/gram",
        "pavan_source_url": "https://example.com/pavan",
    }


class ImmutableHistoryMergeTests(unittest.TestCase):
    """Verify permanent observations cannot silently change."""

    def test_new_observation_is_added(self):
        existing = [
            make_row(
                "2026-07-15",
                "Morning",
                13100,
                104800,
            )
        ]

        observed = [
            make_row(
                "2026-07-16",
                "Morning",
                13125,
                105000,
            )
        ]

        result = merge_history_rows(
            existing,
            observed,
        )

        self.assertEqual(result.added_count, 1)
        self.assertEqual(result.unchanged_count, 0)
        self.assertEqual(len(result.rows), 2)

    def test_exact_duplicate_is_unchanged(self):
        row = make_row(
            "2026-07-16",
            "Morning",
            13125,
            105000,
        )

        result = merge_history_rows(
            [row],
            [dict(row)],
        )

        self.assertEqual(result.added_count, 0)
        self.assertEqual(result.unchanged_count, 1)
        self.assertEqual(len(result.rows), 1)

    def test_additional_session_is_added(self):
        existing = [
            make_row(
                "2026-07-16",
                "Morning",
                13100,
                104800,
            )
        ]

        observed = [
            make_row(
                "2026-07-16",
                "Afternoon",
                13125,
                105000,
            )
        ]

        result = merge_history_rows(
            existing,
            observed,
        )

        self.assertEqual(result.added_count, 1)
        self.assertEqual(result.unchanged_count, 0)
        self.assertEqual(len(result.rows), 2)

        self.assertEqual(
            result.rows[0]["session"],
            "Morning",
        )

        self.assertEqual(
            result.rows[1]["session"],
            "Afternoon",
        )

    def test_changed_price_is_rejected(self):
        existing = [
            make_row(
                "2026-07-16",
                "Morning",
                13100,
                104800,
            )
        ]

        observed = [
            make_row(
                "2026-07-16",
                "Morning",
                13125,
                105000,
            )
        ]

        with self.assertRaises(
            HistoryConflictError
        ) as context:
            merge_history_rows(
                existing,
                observed,
            )

        conflicts = context.exception.conflicts

        self.assertGreater(
            len(conflicts),
            0,
        )

        self.assertEqual(
            conflicts[0].date,
            "2026-07-16",
        )

        self.assertEqual(
            conflicts[0].session,
            "Morning",
        )

        changed_fields = {
            conflict.field
            for conflict in conflicts
        }

        self.assertIn(
            "price_22k_1g_published",
            changed_fields,
        )

        self.assertIn(
            "price_22k_8g_published",
            changed_fields,
        )


if __name__ == "__main__":
    unittest.main()