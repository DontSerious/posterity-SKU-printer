# -*- coding: utf-8 -*-
"""ocr.parse_rows on a packing-list screenshot: Description | SKU | Total | one column per region.

The OCR cells below are laid out by hand after the operator's real screenshots
(Packing list Sandsara Store, 2026-08 .. 11; coordinates rounded from RapidOCR's
boxes on those images). Expected values are what a person reads off that table,
not what the parser returns:

- the quantity to print is the **Total** column; the region columns to its right
  (USA AIR, EU&UK AIR …) split that total and must not be read as the quantity;
- only text in the **SKU** column is a SKU: description text that happens to look
  like one (`White Sand- type A`, a wrapped `… v1-` next to `MT-01`) is not;
- header text above the header row (`USA -CAN`) is not a row.

Run: uv run pytest
"""

import ocr


def _box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def cell(x0, y0, x1, text, h=24):
    return (_box(x0, y0, x1, y0 + h), text, 0.99)


HEADER = [
    cell(680, 50, 756, "USA -CAN"),          # upper line of a two-line region header
    cell(36, 86, 121, "Description"),
    cell(247, 83, 307, "SKU"),
    cell(454, 83, 546, "Total"),
    cell(560, 83, 590, "AIR"),
    cell(660, 83, 690, "SEA"),
    cell(760, 83, 790, "AIR"),
]


def rows_of(body):
    return [(r["sku"], r["qty"], r["qty_detected"]) for r in ocr.parse_rows(HEADER + body)]


def test_quantity_is_the_total_column_not_the_rightmost_region():
    body = [
        cell(36, 120, 200, "White Sand (2 Bags 250gr)"), cell(270, 120, 380, "SS-SAN-NAT"),
        cell(495, 120, 505, "6"), cell(570, 120, 580, "1"), cell(770, 120, 780, "4"),
        cell(36, 150, 200, "Mini black type C"), cell(270, 150, 430, "EU4-SS-mn-GL-AU-BK"),
        cell(495, 150, 505, "2"), cell(670, 150, 680, "1"), cell(770, 150, 780, "1"),
    ]
    assert rows_of(body) == [("SS-SAN-NAT", 6, True), ("EU4-SS-mn-GL-AU-BK", 2, True)]


def test_sku_like_description_text_is_not_the_sku():
    body = [
        # one OCR box; "Sand- type" is glued to "Sand-type", which fits the SKU shape
        cell(36, 120, 240, "Wood Crystal White Sand- type A"), cell(270, 120, 380, "US-WD-CRY-01"),
        cell(495, 120, 505, "5"), cell(570, 120, 580, "5"),
        # a wrapped description ending in "v1-", its own box, left of the SKU column
        cell(36, 150, 197, "(1x) Motor Sandsara v1-"), cell(267, 158, 324, "MT-01"),
        cell(495, 158, 505, "1"), cell(770, 158, 780, "1"),
    ]
    assert rows_of(body) == [("US-WD-CRY-01", 5, True), ("MT-01", 1, True)]


def test_total_header_lost_by_ocr_falls_back_to_first_column_after_sku():
    # The Excel filter arrow sits on "Total" and OCR sometimes returns nothing for it
    # (2026-10-27 sheet). Every sheet puts Total right after SKU, so that column is the quantity.
    header = [c for c in HEADER if c[1] != "Total"]
    body = [cell(36, 120, 200, "SS v2 DW"), cell(270, 120, 430, "A3-SS-DW-AUR-GLA-WLB"),
            cell(495, 120, 505, "6"), cell(570, 120, 580, "1"), cell(670, 120, 680, "3"), cell(770, 120, 780, "2")]
    got = [(r["sku"], r["qty"], r["qty_detected"]) for r in ocr.parse_rows(header + body)]
    assert got == [("A3-SS-DW-AUR-GLA-WLB", 6, True)]


def test_a_title_word_sku_above_the_header_does_not_become_the_header():
    title = [cell(36, 10, 300, "SKU list 2026-11")]
    body = [cell(36, 120, 240, "Wood Crystal White Sand- type A"), cell(270, 120, 380, "US-WD-CRY-01"),
            cell(495, 120, 505, "5"), cell(570, 120, 580, "5")]
    got = [(r["sku"], r["qty"]) for r in ocr.parse_rows(title + HEADER + body)]
    assert got == [("US-WD-CRY-01", 5)]


def test_without_a_total_header_region_columns_are_still_not_sku():
    # Total header lost; this row's SKU cell is empty and a region column holds "EU-UK"
    header = [c for c in HEADER if c[1] != "Total"]
    body = [cell(36, 120, 200, "Note"), cell(560, 120, 600, "EU-UK")]
    assert ocr.parse_rows(header + body) == []


def test_no_header_still_glues_a_dangling_dash_across_a_gap():
    # README: without a SKU header nothing changes; `SAN-NAT-` + `CRY` is ocr._join_dangling_dash's own example
    items = [(_box(100, 0, 180, 24), "SAN-NAT-", 0.99), (_box(215, 0, 245, 24), "CRY", 0.99),
             (_box(300, 0, 310, 24), "2", 0.99)]
    assert [(r["sku"], r["qty"]) for r in ocr.parse_rows(items)] == [("SAN-NAT-CRY", 2)]


def test_qty_header_written_qty_total_in_one_box():
    header = [cell(176, 105, 288, "Description"), cell(476, 103, 527, "SKU"),
              cell(582, 105, 668, "Qty Total"), cell(690, 105, 736, "SEA"), cell(760, 105, 800, "SEA")]
    body = [cell(36, 140, 300, "62cm - Dark Walnut/Metal Legs"), cell(432, 140, 520, "62-SS-DW"),
            cell(620, 140, 630, "7"), cell(705, 140, 715, "4"), cell(775, 140, 785, "1")]
    got = [(r["sku"], r["qty"]) for r in ocr.parse_rows(header + body)]
    assert got == [("62-SS-DW", 7)]
