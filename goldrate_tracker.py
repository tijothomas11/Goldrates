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
import os
import re
import sys
import urllib.request
import xml.sax.saxutils
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

URL = "https://www.keralagold.com/"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) Python GoldRateTracker"


@dataclass
class GoldRateEntry:
    date: dt.date
    price: float

    def as_row(self) -> List[str]:
        return [self.date.isoformat(), f"{self.price:.2f}"]


def fetch_html(url: str = URL) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request) as response:
        return response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")


def extract_rate_from_html(html: str) -> float:
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


def generate_svg(svg_path: Path, entries: List[GoldRateEntry]) -> None:
    if not entries:
        svg_path.write_text("<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"600\" height=\"400\">"
                            "<text x=\"20\" y=\"200\">No data available</text></svg>")
        return

    width, height = 800, 400
    padding = 60

    entries = sorted(entries, key=lambda e: e.date)
    dates = [e.date.toordinal() for e in entries]
    prices = [e.price for e in entries]

    min_date, max_date = min(dates), max(dates)
    min_price, max_price = min(prices), max(prices)
    date_span = max(max_date - min_date, 1)
    price_span = max(max_price - min_price, 1)

    def scale_x(date_ord: int) -> float:
        return padding + (date_ord - min_date) / date_span * (width - 2 * padding)

    def scale_y(price: float) -> float:
        return height - padding - (price - min_price) / price_span * (height - 2 * padding)

    points = [(scale_x(d), scale_y(p)) for d, p in zip(dates, prices)]
    path_d = "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in points)

    price_labels = "".join(
        f"<text x=\"{scale_x(dates[i]):.2f}\" y=\"{scale_y(price)-10:.2f}\" font-size=\"12\" text-anchor=\"middle\">{entries[i].price:.0f}</text>"
        for i, price in enumerate(prices)
    )

    svg_content = f"""
<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{width}\" height=\"{height}\">
  <rect width=\"100%\" height=\"100%\" fill=\"#ffffff\"/>
  <g stroke=\"#e0e0e0\">
    <line x1=\"{padding}\" y1=\"{height-padding}\" x2=\"{width-padding}\" y2=\"{height-padding}\"/>
    <line x1=\"{padding}\" y1=\"{padding}\" x2=\"{padding}\" y2=\"{height-padding}\"/>
  </g>
  <path d=\"{path_d}\" fill=\"none\" stroke=\"#c79a00\" stroke-width=\"2.5\"/>
  {price_labels}
  <text x=\"{width/2}\" y=\"30\" font-size=\"18\" text-anchor=\"middle\">Kerala gold rate history</text>
</svg>
"""
    svg_path.write_text(svg_content.strip())


def update_history(html: str, csv_path: Path, xlsx_path: Path, svg_path: Path, date: dt.date | None = None) -> GoldRateEntry:
    rate = extract_rate_from_html(html)
    target_date = date or dt.date.today()

    entries = load_history(csv_path)
    filtered = [entry for entry in entries if entry.date != target_date]
    new_entry = GoldRateEntry(date=target_date, price=rate)
    filtered.append(new_entry)

    save_history(csv_path, filtered)
    write_xlsx(xlsx_path, sorted(filtered, key=lambda e: e.date))
    generate_svg(svg_path, filtered)
    return new_entry


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Track Kerala gold rates and store them in Excel.")
    parser.add_argument("--csv", dest="csv_path", default="gold_rates.csv", help="CSV file for storing history.")
    parser.add_argument("--excel", dest="excel_path", default="gold_rates.xlsx", help="Excel (.xlsx) file to write.")
    parser.add_argument("--graph", dest="graph_path", default="gold_rates.svg", help="SVG graph output path.")
    parser.add_argument("--html-file", dest="html_file", help="Use a local HTML file instead of fetching from the internet.")
    parser.add_argument("--date", dest="date", help="Override the entry date (YYYY-MM-DD).")
    parser.add_argument("--quiet", action="store_true", help="Suppress informational output.")
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        target_date = dt.date.fromisoformat(args.date) if args.date else None
    except ValueError:
        print("Invalid date format. Use YYYY-MM-DD.")
        return 1

    try:
        if args.html_file:
            html = Path(args.html_file).read_text(encoding="utf-8")
        else:
            html = fetch_html()
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to retrieve gold rate page: {exc}")
        return 1

    try:
        new_entry = update_history(html, Path(args.csv_path), Path(args.excel_path), Path(args.graph_path), date=target_date)
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to update history: {exc}")
        return 1

    if not args.quiet:
        print(f"Recorded Kerala gold rate {new_entry.price:.2f} for {new_entry.date.isoformat()}.")
        print(f"CSV file: {os.path.abspath(args.csv_path)}")
        print(f"Excel file: {os.path.abspath(args.excel_path)}")
        print(f"Graph: {os.path.abspath(args.graph_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
