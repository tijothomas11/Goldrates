"""Tests for the sparse yearly Kerala gold history parser."""

import importlib.util
import unittest
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PROJECT_ROOT
    / "scripts"
    / "create_yearly_history.py"
)


def load_yearly_module():
    """Load the yearly-history script as a testable module."""

    spec = importlib.util.spec_from_file_location(
        "create_yearly_history",
        SCRIPT_PATH,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            "Could not load create_yearly_history.py"
        )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


yearly_history = load_yearly_module()


class YearlyHistoryTests(unittest.TestCase):
    """Verify sparse yearly reference values are parsed safely."""

    def test_yearly_reference_rows(self):
        html = """
        <table>
          <tr>
            <th>Year</th>
            <th>Date</th>
            <th>Price of 1 Pavan Gold (Rs.)</th>
          </tr>

          <tr>
            <td>1925</td>
            <td>31-March-25</td>
            <td>13.75</td>
          </tr>

          <tr>
            <td>1930</td>
            <td>31-March-30</td>
            <td>13.57</td>
          </tr>

          <tr>
            <td></td>
            <td></td>
            <td></td>
          </tr>

          <tr>
            <td>
              <a href="monthly-gold-prices-2009.htm">
                2009
              </a>
            </td>
            <td>31-March-09</td>
            <td>11,077</td>
          </tr>

          <tr>
            <td>
              <a href="monthly-gold-prices.htm">
                2026
              </a>
            </td>
            <td>31-March-26</td>
            <td>109640</td>
          </tr>
        </table>
        """

        rows = yearly_history.parse_yearly_history(
            html
        )

        self.assertEqual(len(rows), 4)

        self.assertEqual(
            rows[0]["year"],
            "1925",
        )

        self.assertEqual(
            rows[0]["date"],
            "1925-03-31",
        )

        self.assertEqual(
            rows[0]["price_22k_8g_published"],
            "13.75",
        )

        self.assertEqual(
            rows[0]["price_22k_1g_calculated"],
            "1.71875",
        )

        self.assertEqual(
            rows[1]["year"],
            "1930",
        )

        self.assertEqual(
            rows[1]["price_22k_1g_calculated"],
            "1.69625",
        )

        self.assertEqual(
            rows[2]["year"],
            "2009",
        )

        self.assertEqual(
            rows[2]["price_22k_8g_published"],
            "11077",
        )

        self.assertEqual(
            rows[2]["price_22k_1g_calculated"],
            "1384.625",
        )

        self.assertEqual(
            rows[3]["year"],
            "2026",
        )

        self.assertEqual(
            rows[3]["price_22k_8g_published"],
            "109640",
        )

        self.assertEqual(
            rows[3]["price_22k_1g_calculated"],
            "13705",
        )

    def test_decimal_calculation_is_exact(self):
        pavan_price = Decimal("13.57")

        expected = Decimal("1.69625")

        self.assertEqual(
            pavan_price
            / yearly_history.PAVAN_GRAMS,
            expected,
        )

    def test_non_march_31_rows_are_ignored(self):
        html = """
        <table>
          <tr>
            <td>2025</td>
            <td>30-March-25</td>
            <td>67400</td>
          </tr>

          <tr>
            <td>2026</td>
            <td>31-March-26</td>
            <td>109640</td>
          </tr>
        </table>
        """

        rows = yearly_history.parse_yearly_history(
            html
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["year"],
            "2026",
        )


if __name__ == "__main__":
    unittest.main()