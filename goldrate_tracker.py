"""
Daily Kerala gold rate tracker.

Fetches the current Kerala gold rate from https://www.keralagold.com/ and stores the
values in CSV and Excel formats. A simple SVG line chart is also produced from the
collected history.

The script relies only on the Python standard library so it can run in constrained
environments.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import math
import os
import re
import sys
import urllib.request
import xml.sax.saxutils
import zipfile
from html.parser import HTMLParser
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

URL = "https://www.keralagold.com/kerala-gold-rate-per-gram.htm"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) Python GoldRateTracker"

# The tracker collects the latest gold rates from the KeralaGold website.
# The values are parsed from HTML and stored in CSV or Excel form.

@dataclass
class GoldRateEntry:
    date: dt.date
    price: float

    def as_row(self) -> List[str]:
        return [self.date.isoformat(), f"{self.price:.2f}"]
    
@dataclass
class HistoryRecord:
    """Represents a single historical gold-rate entry extracted from the KeralaGold history table. 
    Examples:   2026-07-01 Morning 12905
                2026-07-01 Evening 13040
    """
    date: dt.date
    session: str | None
    price: int

def fetch_html(url: str = URL) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request) as response:
        return response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")

def parse_price(text: str) -> int:
    """
    Extract a numeric gold price from text.

    Examples:
        'Rs. 13,100' -> 13100
        '13040' -> 13040
    """
    match = re.search(r"(\d[\d,]*)", text)

    if not match:
        raise ValueError(f"Could not extract price from: {text}")

    return int(match.group(1).replace(",", ""))

class KeralaHistoryTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        # Store text from every table cell in the history page.
        self.cells = []
        self.current_cell = []
        self.in_td = False

    def handle_starttag(self, tag, attrs):
        if tag == "td":
            # We are now inside a table cell. Start collecting the text.
            self.in_td = True
            self.current_cell = []

    def handle_endtag(self, tag):
        if tag == "td":
            self.in_td = False

            text = " ".join(
                part.strip()
                for part in self.current_cell
                if part.strip()
            )

            self.cells.append(text)

    def handle_data(self, data):
        if self.in_td:
            self.current_cell.append(data)

def parse_history_table(html: str) -> list[HistoryRecord]:
        # Repair a known malformed KeralaGold pattern such as:
    #
    # <span class="kg2"4575</span>
    #
    # The source is missing the closing ">" after the class
    # attribute. Convert it to:
    #
    # <span class="kg2">4575</span>
    html = re.sub(
        r'(<span\s+class=["\']kg2["\'])'
        r'(\d[\d,]*)'
        r'(</span>)',
        r"\1>\2\3",
        html,
        flags=re.IGNORECASE,
    )
    """
    Parse the KeralaGold historical rate table.
    Returns:
    A list of HistoryRecord objects containing:
        - date
        - session label
        - price
    Records are returned in the same order they appear on the website.
    """
    parser = KeralaHistoryTableParser()
    parser.feed(html)

    # Remove any blank cells that can happen in malformed HTML.
    cells = [cell for cell in parser.cells if cell]

    records: list[HistoryRecord] = []

    i = 0

    # The page stores dates and prices in adjacent table cells.
    # We parse them two at a time: one date cell followed by one price cell.
    while i < len(cells) - 1:
        date_cell = cells[i]
        price_cell = cells[i + 1]

        # The page alternates date cells and price cells.
        # We only accept a pair when the first cell contains a date string.
        date_match = re.search(
            r"\d{1,2}-[A-Za-z]{3}-\d{2}",
            date_cell,
        )

        if not date_match:
            i += 1
            continue

        try:
            record = HistoryRecord(
                date=parse_date(date_match.group(0)),
                session=extract_session(date_cell),
                price=parse_price(price_cell),
            )

            records.append(record)

        except ValueError:
            pass

        i += 2

    return records

def save_detailed_history_csv(
    csv_path: Path,
    records: list[HistoryRecord],
) -> None:
    """
    Save all parsed historical records to a CSV file.

    Every website entry is preserved, including multiple
    rates recorded on the same date.

    Columns:
        date, session, price
    """
    with csv_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)

        # Write the column headings.
        writer.writerow(["date", "session", "price"])

        # Write a row for each parsed history record.

        # Preserve records in the same order as the website.
        for record in records:
            writer.writerow(
                [
                    record.date.isoformat(),
                    record.session or "",
                    record.price,
                ]
            )

def parse_date(text: str) -> dt.date:
    """
    Convert KeralaGold date strings into Python dates.

    Example:
        '13-Jul-26' -> datetime.date(2026, 7, 13)
    """
    return dt.datetime.strptime(text.strip(), "%d-%b-%y").date()


def extract_session(text: str) -> str | None:
    """
    Extract the session label from a table row.

    Examples:
        Morning
        Forenoon
        Afternoon
        Evening
        Today
        Yesterday
    """
    # The history table can include descriptive labels like Today or Morning.
    match = re.search(
        r"(Morning|Afternoon|Evening|Forenoon|Today|Yesterday)",
        text,
        re.IGNORECASE,
    )

    return match.group(1) if match else None

def extract_rate_from_html(html: str) -> float:
    # Lowercasing makes pattern matching easier for currency labels.
    normalized = html.lower()
    price_pattern = re.compile(r"[₹rs\.]*\s*([0-9][0-9,]*(?:\.[0-9]+)?)")

    def parse_price(raw: str) -> float:
        cleaned = re.sub(r"[^0-9.]", "", raw.replace(",", ""))
        if not cleaned:
            raise ValueError("No digits found in price text")
        return float(cleaned)

    explicit_pattern = re.compile(
        r"1\s*(?:gram|gm|g)\b.{0,180}?([₹rs\.]*\s*[0-9][0-9,]*(?:\.[0-9]+)?)",
        re.IGNORECASE | re.DOTALL,
    )
    explicit_match = explicit_pattern.search(html)
    if explicit_match:
        value = parse_price(explicit_match.group(1))
        if value >= 100:
            return value

    # If not found explicitly, search for any '1 gram' context and look
    # for a nearby numeric price in the surrounding text.
    gram_positions = [m.start() for m in re.finditer(r"1\s*(?:gram|gm|g)", normalized)]

    def find_price_near(index: int) -> float | None:
        window = html[max(index - 250, 0): index + 250]
        for match in price_pattern.finditer(window):
            value = parse_price(match.group(1))
            if value >= 100:  # filter out the "1" from "1 gram"
                return value
        return None

    for pos in gram_positions:
        price = find_price_near(pos)
        if price is not None:
            return price

    fallback_match = price_pattern.search(html)
    if not fallback_match:
        raise ValueError("Could not locate a gold rate in the supplied HTML")
    return parse_price(fallback_match.group(1))


def load_history(csv_path: Path) -> List[GoldRateEntry]:
    # Load a simplified two-column daily history CSV if the detailed
    # permanent history is unavailable.
    entries: List[GoldRateEntry] = []
    if not csv_path.exists():
        return entries
    with csv_path.open(newline="") as fp:
        reader = csv.reader(fp)
        for row in reader:
            if len(row) != 2:
                continue
            try:
                date_value = dt.date.fromisoformat(row[0])
                price_value = float(row[1])
            except ValueError:
                continue
            entries.append(GoldRateEntry(date=date_value, price=price_value))
    return entries



PERMANENT_HISTORY_FIELDS = [
    "date",
    "session",
    "price_22k_1g_published",
    "price_22k_8g_published",
    "price_22k_1g_from_pavan",
    "difference_22k",
    "normalized_price_22k_1g",
    "gram_source_url",
    "pavan_source_url",
]


SESSION_ORDER = {
    "": 0,
    "Early Morning": 1,
    "Morning": 2,
    "Forenoon": 3,
    "Noon": 4,
    "Afternoon": 5,
    "Evening": 6,
    "Night": 7,
}


def load_permanent_history(csv_path: Path):
    """
    Load the detailed permanent historical dataset.

    The permanent file contains multiple observations on some
    dates. For the simplified CSV, Excel workbook, and SVG,
    the latest available session on each date is selected.

    Returns:
        daily_entries:
            One GoldRateEntry per calendar date.

        observation_count:
            Total number of detailed source observations.

        skipped_count:
            Number of malformed or incomplete rows skipped.
    """

    if not csv_path.exists():
        return [], 0, 0

    with csv_path.open(
        "r",
        encoding="utf-8",
        newline="",
    ) as file:
        reader = csv.DictReader(file)

        if reader.fieldnames != PERMANENT_HISTORY_FIELDS:
            raise ValueError(
                "Permanent history has an unexpected schema. "
                f"Expected {PERMANENT_HISTORY_FIELDS}; "
                f"found {reader.fieldnames}."
            )

        rows = list(reader)

    latest_by_date = {}
    skipped_count = 0

    for row_number, row in enumerate(
        rows,
        start=2,
    ):
        date_text = row.get(
            "date",
            "",
        ).strip()

        session = row.get(
            "session",
            "",
        ).strip()

        price_text = row.get(
            "normalized_price_22k_1g",
            "",
        ).strip()

        if not date_text or not price_text:
            skipped_count += 1
            continue

        try:
            date_value = dt.date.fromisoformat(
                date_text
            )

            price_value = float(
                price_text.replace(",", "")
            )

        except ValueError as exc:
            raise ValueError(
                "Invalid permanent-history row "
                f"{row_number}: {exc}"
            ) from exc

        if price_value <= 0:
            raise ValueError(
                "Invalid permanent-history price on "
                f"row {row_number}: {price_value}"
            )

        session_rank = SESSION_ORDER.get(
            session,
            50,
        )

        existing = latest_by_date.get(
            date_value
        )

        if (
            existing is None
            or session_rank >= existing[0]
        ):
            latest_by_date[date_value] = (
                session_rank,
                GoldRateEntry(
                    date=date_value,
                    price=price_value,
                ),
            )

    daily_entries = [
        value[1]
        for _, value in sorted(
            latest_by_date.items()
        )
    ]

    return (
        daily_entries,
        len(rows),
        skipped_count,
    )

def save_history(csv_path: Path, entries: Iterable[GoldRateEntry]) -> None:
    with csv_path.open("w", newline="") as fp:
        writer = csv.writer(fp)
        for entry in sorted(entries, key=lambda e: e.date):
            writer.writerow(entry.as_row())


def column_letter(idx: int) -> str:
    letters = ""
    while idx >= 0:
        idx, remainder = divmod(idx, 26)
        letters = chr(65 + remainder) + letters
        idx -= 1
    return letters


def write_xlsx(xlsx_path: Path, entries: List[GoldRateEntry]) -> None:
    sheet_rows = [("Date", "Price (INR per gram)"), *[(e.date.isoformat(), e.price) for e in entries]]

    def cell_xml(row: int, col: int, value) -> str:
        cell_ref = f"{column_letter(col)}{row}"
        if isinstance(value, (int, float)):
            return f'<c r="{cell_ref}"><v>{value}</v></c>'
        escaped = xml.sax.saxutils.escape(str(value))
        return f'<c r="{cell_ref}" t="str"><v>{escaped}</v></c>'

    sheet_data_rows = []
    for idx, row in enumerate(sheet_rows, start=1):
        cells = "".join(cell_xml(idx, col, val) for col, val in enumerate(row))
        sheet_data_rows.append(f"<row r=\"{idx}\">{cells}</row>")
    sheet_xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\">"
        "<sheetData>"
        + "".join(sheet_data_rows)
        + "</sheetData></worksheet>"
    )

    workbook_xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<workbook xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" "
        "xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\">"
        "<sheets><sheet name=\"GoldRates\" sheetId=\"1\" r:id=\"rId1\"/></sheets></workbook>"
    )

    workbook_rels_xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<Relationships xmlns=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\">"
        "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet\" Target=\"worksheets/sheet1.xml\"/>"
        "</Relationships>"
    )

    content_types_xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">"
        "<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>"
        "<Default Extension=\"xml\" ContentType=\"application/xml\"/>"
        "<Override PartName=\"/xl/workbook.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml\"/>"
        "<Override PartName=\"/xl/worksheets/sheet1.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml\"/>"
        "</Types>"
    )

    rels_xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
        "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"xl/workbook.xml\"/>"
        "</Relationships>"
    )

    with zipfile.ZipFile(xlsx_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", rels_xml)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def _nice_ticks(vmin: float, vmax: float, n: int = 5) -> List[float]:
    """Generate nicely rounded tick values spanning vmin to vmax."""
    span = vmax - vmin
    if span <= 0:
        return [round(vmin, 2)]
    raw_step = span / n
    exp = math.floor(math.log10(raw_step))
    base = 10 ** exp
    fraction = raw_step / base
    if fraction <= 1:
        nice_step = base
    elif fraction <= 2:
        nice_step = 2 * base
    elif fraction <= 5:
        nice_step = 5 * base
    else:
        nice_step = 10 * base
    start = math.floor(vmin / nice_step) * nice_step
    end = math.ceil(vmax / nice_step) * nice_step
    ticks: List[float] = []
    v = start
    while v <= end + nice_step * 0.001:
        ticks.append(round(v, 2))
        v += nice_step
    return ticks


def _format_rupee(value: float) -> str:
    return f"\u20b9{value:,.0f}"


def _format_date_short(d: dt.date) -> str:
    return f"{d.strftime('%b')} {d.day}"


def _thin_indices(n: int, max_count: int) -> List[int]:
    """Return a subset of indices 0..n-1, keeping first and last, capped at max_count."""
    if n <= max_count:
        return list(range(n))
    step = math.ceil(n / max_count)
    indices = list(range(0, n, step))
    if indices[-1] != n - 1:
        indices.append(n - 1)
    return indices


def generate_svg(svg_path: Path, entries: List[GoldRateEntry]) -> None:
    if not entries:
        svg_path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="500" font-family="sans-serif">'
            '<rect width="100%" height="100%" fill="#fafafa"/>'
            '<text x="450" y="250" font-size="18" text-anchor="middle" fill="#999">No data available</text>'
            '</svg>',
            encoding="utf-8",
        )
        return

    entries = sorted(entries, key=lambda e: e.date)
    dates = [e.date.toordinal() for e in entries]
    prices = [e.price for e in entries]
    n = len(entries)

    width, height = 900, 500
    title_y = 32
    subtitle_y = 54
    chart_left = 80
    chart_right = width - 30
    chart_top = 80
    chart_bottom = height - 65
    chart_w = chart_right - chart_left
    chart_h = chart_bottom - chart_top

    min_price = min(prices)
    max_price = max(prices)
    avg_price = sum(prices) / len(prices)

    y_ticks = _nice_ticks(min_price, max_price, 5)
    if len(y_ticks) < 2:
        y_min = min_price - 50
        y_max = max_price + 50
        y_ticks = _nice_ticks(y_min, y_max, 5)
    else:
        y_min = y_ticks[0]
        y_max = y_ticks[-1]

    min_date = min(dates)
    max_date = max(dates)
    date_span = max(max_date - min_date, 1)

    def scale_x(date_ord: int) -> float:
        return chart_left + (date_ord - min_date) / date_span * chart_w

    def scale_y(price: float) -> float:
        return chart_bottom - (price - y_min) / (y_max - y_min) * chart_h

    points = [(scale_x(d), scale_y(p)) for d, p in zip(dates, prices)]

    current_idx = n - 1
    min_idx = prices.index(min_price)
    max_idx = prices.index(max_price)
    special_indices = {current_idx, min_idx, max_idx}

    path_d = ""
    if n >= 2:
        path_d = "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in points)

    area_d = ""
    if n >= 2:
        area_d = (
            f"M {points[0][0]:.2f},{chart_bottom:.2f} "
            + " ".join(f"L {x:.2f},{y:.2f}" for x, y in points)
            + f" L {points[-1][0]:.2f},{chart_bottom:.2f} Z"
        )

    grid_svg = ""
    y_label_svg = ""
    for tick in y_ticks:
        y = scale_y(tick)
        if chart_top - 5 <= y <= chart_bottom + 5:
            grid_svg += f'<line x1="{chart_left}" y1="{y:.2f}" x2="{chart_right}" y2="{y:.2f}" stroke="#f0f0f0" stroke-width="1"/>'
            y_label_svg += f'<text x="{chart_left - 10}" y="{y + 4:.2f}" font-size="12" text-anchor="end" fill="#666">{_format_rupee(tick)}</text>'

    max_x_labels = max(int(chart_w / 65), 2)
    x_label_indices = set(_thin_indices(n, max_x_labels))
    x_label_svg = ""
    for i in sorted(x_label_indices):
        x = scale_x(dates[i])
        x_label_svg += f'<text x="{x:.2f}" y="{chart_bottom + 22:.2f}" font-size="11" text-anchor="middle" fill="#666">{_format_date_short(entries[i].date)}</text>'

    max_price_labels = max(int(chart_w / 55), 3)
    other_indices = [i for i in range(n) if i not in special_indices]
    other_label_count = max(max_price_labels - len(special_indices), 0)
    other_label_set = set(_thin_indices(len(other_indices), other_label_count)) if other_label_count > 0 else set()
    # The summary box already displays the current and maximum
    # values. Avoid placing duplicate labels near the crowded
    # right edge of the chart.
    labeled = {
        index
        for index in special_indices
        if index not in {
            current_idx,
            max_idx,
        }
    }

    # Avoid ordinary price labels near the latest observations.
    # The Summary box already communicates the current value.
    right_edge_limit = n - max(
        30,
        int(n * 0.03),
    )

    for j, i in enumerate(other_indices):
        if (
            j in other_label_set
            and i < right_edge_limit
        ):
            labeled.add(i)

    label_color: dict[int, str] = {}
    label_color[min_idx] = "#2e7d32"
    label_color[max_idx] = "#c62828"
    label_color[current_idx] = "#e65100"
    price_label_svg = ""
    for i in sorted(labeled):
        x, y = points[i]
        color = label_color.get(i, "#555")
        price_label_svg += f'<text x="{x:.2f}" y="{y - 12:.2f}" font-size="11" text-anchor="middle" fill="{color}" font-weight="bold">{_format_rupee(prices[i])}</text>'

    marker_svg = ""
    if n <= 50:
        for i in range(n):
            if i in special_indices:
                continue
            x, y = points[i]
            marker_svg += f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3" fill="#d4a017" opacity="0.6"/>'

    if n > 1:
        if min_idx != current_idx:
            mx, my = points[min_idx]
            marker_svg += f'<rect x="{mx-5:.2f}" y="{my-5:.2f}" width="10" height="10" fill="#2e7d32" stroke="#fff" stroke-width="1.5" transform="rotate(45 {mx:.2f} {my:.2f})"/>'
        if max_idx != current_idx:
            mx2, my2 = points[max_idx]
            marker_svg += f'<rect x="{mx2-5:.2f}" y="{my2-5:.2f}" width="10" height="10" fill="#c62828" stroke="#fff" stroke-width="1.5" transform="rotate(45 {mx2:.2f} {my2:.2f})"/>'

    cx, cy = points[current_idx]
    marker_svg += f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="6" fill="#e65100" stroke="#fff" stroke-width="2"/>'

    stats_x = chart_left + 15
    stats_y = chart_top + 10
    stats_w = 165
    stats_h = 108
    stats_lines = [
        ("Current", _format_rupee(prices[current_idx]), "#e65100"),
        ("Minimum", _format_rupee(min_price), "#2e7d32"),
        ("Maximum", _format_rupee(max_price), "#c62828"),
        ("Average", _format_rupee(avg_price), "#666"),
    ]
    stats_svg = f'<rect x="{stats_x}" y="{stats_y}" width="{stats_w}" height="{stats_h}" fill="#ffffff" stroke="#e0e0e0" stroke-width="1" rx="6"/>'
    stats_svg += f'<text x="{stats_x + stats_w/2}" y="{stats_y + 18}" font-size="13" text-anchor="middle" fill="#555" font-weight="bold">Summary</text>'
    stats_svg += f'<line x1="{stats_x + 10}" y1="{stats_y + 23}" x2="{stats_x + stats_w - 10}" y2="{stats_y + 23}" stroke="#eee" stroke-width="1"/>'
    for j, (label, value, color) in enumerate(stats_lines):
        ly = stats_y + 42 + j * 16
        marker_x = stats_x + 15
        label_x = stats_x + 28

        if label == "Current":
            stats_svg += (
                f'<circle '
                f'cx="{marker_x}" '
                f'cy="{ly - 4}" '
                f'r="4" '
                f'fill="#e65100" '
                f'stroke="#ffffff" '
                f'stroke-width="1"/>'
            )

        elif label in {"Minimum", "Maximum"}:
            stats_svg += (
                f'<rect '
                f'x="{marker_x - 4}" '
                f'y="{ly - 8}" '
                f'width="8" '
                f'height="8" '
                f'fill="{color}" '
                f'stroke="#ffffff" '
                f'stroke-width="1" '
                f'transform="rotate('
                f'45 {marker_x} {ly - 4}'
                f')"/>'
            )

        stats_svg += (
            f'<text '
            f'x="{label_x}" '
            f'y="{ly}" '
            f'font-size="12" '
            f'fill="#888">'
            f'{label}'
            f'</text>'
        )

        stats_svg += (
            f'<text '
            f'x="{stats_x + stats_w - 12}" '
            f'y="{ly}" '
            f'font-size="12" '
            f'text-anchor="end" '
            f'fill="{color}" '
            f'font-weight="bold">'
            f'{value}'
            f'</text>'
        )

    if n == 1:
        subtitle = entries[0].date.isoformat()
    else:
        subtitle = f"{entries[0].date.strftime('%b %d, %Y')} \u2013 {entries[-1].date.strftime('%b %d, %Y')}  \u00b7  {n} data points"

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" font-family="sans-serif">\n'
        f'  <rect width="100%" height="100%" fill="#fafafa"/>\n'
        f'  <defs>\n'
        f'    <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">\n'
        f'      <stop offset="0%" stop-color="#d4a017" stop-opacity="0.25"/>\n'
        f'      <stop offset="100%" stop-color="#d4a017" stop-opacity="0.02"/>\n'
        f'    </linearGradient>\n'
        f'  </defs>\n'
        f'  <text x="{width/2}" y="{title_y}" font-size="20" text-anchor="middle" fill="#333" font-weight="bold">Kerala Gold Rate History</text>\n'
        f'  <text x="{width/2}" y="{subtitle_y}" font-size="13" text-anchor="middle" fill="#888">{subtitle}</text>\n'
        f'  <g>{grid_svg}</g>\n'
        + (f'  <path d="{area_d}" fill="url(#areaGrad)"/>\n' if area_d else '')
        + (f'  <path d="{path_d}" fill="none" stroke="#d4a017" stroke-width="2.5"/>\n' if path_d else '')
        + f'  <line x1="{chart_left}" y1="{chart_bottom}" x2="{chart_right}" y2="{chart_bottom}" stroke="#ccc" stroke-width="1.5"/>\n'
        + f'  <line x1="{chart_left}" y1="{chart_top}" x2="{chart_left}" y2="{chart_bottom}" stroke="#ccc" stroke-width="1.5"/>\n'
        + f'  <g>{y_label_svg}</g>\n'
        + f'  <g>{x_label_svg}</g>\n'
        + f'  <g>{price_label_svg}</g>\n'
        + f'  <g>{marker_svg}</g>\n'
        + f'  {stats_svg}\n'
        + f'</svg>'
    )

    svg_path.write_text(svg, encoding="utf-8")



def update_history(
    html: str,
    permanent_history_path: Path,
    csv_path: Path,
    xlsx_path: Path,
    svg_path: Path,
    date: dt.date | None = None,
):
    """
    Regenerate simplified outputs from permanent history.

    The detailed nine-column permanent history is read-only.
    This function writes only the simplified CSV, Excel file,
    and SVG chart.
    """

    live_records = parse_history_table(html)

    today_record = next(
        (
            record
            for record in live_records
            if record.session
            and record.session.lower() == "today"
        ),
        None,
    )

    if today_record is None:
        raise ValueError(
            "Could not find the row marked 'Today' "
            "in the KeralaGold history table."
        )

    rate = float(today_record.price)
    target_date = date or dt.date.today()

    (
        entries,
        observation_count,
        skipped_count,
    ) = load_permanent_history(
        permanent_history_path
    )

    used_permanent_history = bool(entries)

    if not entries:
        entries = load_history(csv_path)
        observation_count = len(entries)
        skipped_count = 0

    # The live value takes precedence for the target date.
    # This avoids duplicate entries for the same day.
    filtered = [
        entry
        for entry in entries
        if entry.date != target_date
    ]

    new_entry = GoldRateEntry(
        date=target_date,
        price=rate,
    )

    filtered.append(new_entry)

    filtered.sort(
        key=lambda entry: entry.date
    )

    save_history(
        csv_path,
        filtered,
    )

    write_xlsx(
        xlsx_path,
        filtered,
    )

    generate_svg(
        svg_path,
        filtered,
    )

    return (
        new_entry,
        observation_count,
        len(filtered),
        skipped_count,
        used_permanent_history,
    )


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Track Kerala gold rates and generate "
            "CSV, Excel, and SVG outputs."
        )
    )

    parser.add_argument(
        "--history",
        dest="history_path",
        default="data/gold_rates_history.csv",
        help=(
            "Detailed permanent history used as the "
            "read-only source for generated outputs."
        ),
    )

    parser.add_argument(
        "--csv",
        dest="csv_path",
        default="gold_rates.csv",
        help=(
            "Simplified two-column daily CSV output."
        ),
    )

    parser.add_argument(
        "--excel",
        dest="excel_path",
        default="gold_rates.xlsx",
        help="Excel (.xlsx) output path.",
    )

    parser.add_argument(
        "--graph",
        dest="graph_path",
        default="gold_rates.svg",
        help="SVG graph output path.",
    )

    parser.add_argument(
        "--html-file",
        dest="html_file",
        help=(
            "Use a local HTML file instead of "
            "fetching from the internet."
        ),
    )

    parser.add_argument(
        "--date",
        dest="date",
        help=(
            "Override the live entry date "
            "(YYYY-MM-DD)."
        ),
    )

    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress informational output.",
    )

    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(
        argv or sys.argv[1:]
    )

    try:
        target_date = (
            dt.date.fromisoformat(args.date)
            if args.date
            else None
        )

    except ValueError:
        print(
            "Invalid date format. Use YYYY-MM-DD."
        )
        return 1

    permanent_history_path = Path(
        args.history_path
    )

    # Protect the permanent detailed history from accidentally
    # being selected as the simplified two-column CSV output.
    try:
        if (
            permanent_history_path.resolve()
            == Path(args.csv_path).resolve()
        ):
            print(
                "ERROR: --history and --csv must not "
                "refer to the same file."
            )
            print(
                "The permanent detailed history is "
                "read-only during normal tracker runs."
            )
            return 1

    except OSError:
        pass

    try:
        if args.html_file:
            html = Path(
                args.html_file
            ).read_text(
                encoding="utf-8"
            )
        else:
            html = fetch_html()

    except Exception as exc:  # noqa: BLE001
        print(
            f"Failed to retrieve gold rate page: "
            f"{exc}"
        )
        return 1

    # Parse and preserve the current webpage observations
    # separately from the permanent historical dataset.
    live_records = parse_history_table(html)

    live_page_csv_path = Path(
        "gold_rates_live_page.csv"
    )

    save_detailed_history_csv(
        live_page_csv_path,
        live_records,
    )

    if not args.quiet:
        print(
            f"Found {len(live_records)} live-page "
            "historical observations."
        )
        print(
            "Live-page CSV: "
            f"{live_page_csv_path.resolve()}"
        )

    try:
        (
            new_entry,
            observation_count,
            daily_count,
            skipped_count,
            used_permanent_history,
        ) = update_history(
            html,
            permanent_history_path,
            Path(args.csv_path),
            Path(args.excel_path),
            Path(args.graph_path),
            date=target_date,
        )

    except Exception as exc:  # noqa: BLE001
        print(
            f"Failed to update generated outputs: "
            f"{exc}"
        )
        return 1

    if not args.quiet:
        if used_permanent_history:
            print(
                f"Loaded {observation_count} permanent "
                "historical observations."
            )
            print(
                "Permanent history was read from: "
                f"{permanent_history_path.resolve()}"
            )
        else:
            print(
                "Permanent history was unavailable or "
                "empty; used the existing simplified "
                "CSV as a fallback."
            )
            print(
                f"Fallback records loaded: "
                f"{observation_count}"
            )

        if skipped_count:
            print(
                f"Skipped malformed permanent-history "
                f"rows: {skipped_count}"
            )

        print(
            f"Prepared {daily_count} daily "
            "chart records."
        )

        print(
            f"Recorded Kerala gold rate "
            f"{new_entry.price:.2f} for "
            f"{new_entry.date.isoformat()}."
        )

        print(
            f"CSV file: "
            f"{os.path.abspath(args.csv_path)}"
        )

        print(
            f"Excel file: "
            f"{os.path.abspath(args.excel_path)}"
        )

        print(
            f"Graph: "
            f"{os.path.abspath(args.graph_path)}"
        )

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
