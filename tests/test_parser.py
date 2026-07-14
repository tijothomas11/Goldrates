"""Tests for KeralaGold historical-table parsing."""

import datetime as dt
import unittest
from pathlib import Path

from goldrate_tracker import parse_history_table


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class HistoryParserTests(unittest.TestCase):
    """Verify historical observations are parsed correctly."""

    def test_history_fixture(self):
        html_path = FIXTURES_DIR / "history_page.html"
        html = html_path.read_text(encoding="utf-8")

        records = parse_history_table(html)

        self.assertEqual(len(records), 4)

        self.assertEqual(
            records[0].date,
            dt.date(2026, 7, 1),
        )
        self.assertIsNone(records[0].session)
        self.assertEqual(records[0].price, 12905)

        self.assertEqual(
            records[1].date,
            dt.date(2026, 7, 1),
        )
        self.assertEqual(
            records[1].session,
            "Evening",
        )
        self.assertEqual(records[1].price, 13040)

        self.assertEqual(
            records[2].date,
            dt.date(2026, 7, 2),
        )
        self.assertEqual(
            records[2].session,
            "Morning",
        )
        self.assertEqual(records[2].price, 13250)

        self.assertEqual(
            records[3].date,
            dt.date(2026, 7, 13),
        )
        self.assertEqual(
            records[3].session,
            "Today",
        )
        self.assertEqual(records[3].price, 13100)

    def test_malformed_price_markup(self):
        html = """
        <table>
          <tr>
            <td><span class="kg2">9-Jul-20</span></td>
            <td><span class="kg2"4575</span></td>
          </tr>
        </table>
        """

        records = parse_history_table(html)

        self.assertEqual(len(records), 1)
        self.assertEqual(
            records[0].date,
            dt.date(2020, 7, 9),
        )
        self.assertIsNone(records[0].session)
        self.assertEqual(records[0].price, 4575)


if __name__ == "__main__":
    unittest.main()