# -*- coding: utf-8 -*-
"""ocr.parse_text / normalize_sku / parse_rows, against the quantity-notation table in README.md.

Every expected value below is taken from README.md (cited per row), not from running
the parser: the README is the specification the operator reads, the parser is what
is under test. A row that only the code documents is not here.

    README.md:18-20  the three example lines of the paste box
    README.md:22     数量支持 X3 / x3 / ×3 / 3X / (3) / 纯数字 / 紧贴写法 PHC-ELE-BKX7
    README.md:23     Excel「描述 ⇥ SKU ⇥ 数量」整行；不写数量按 1 张算并标黄
    README.md:24     认不出 SKU 的行不会静默丢掉，会列出来
    README.md:25-27  「按原文」: SC,ABC stays SC,ABC; only an explicit trailing X3 / (3) is a quantity
    README.md:28     标黄 = 置信度低或数量没读到
    README.md:245    "min_score": 0.85 — rows below it are yellow
    README.md:249    default SKU shape; case kept (UK4-SS-mn-GL-AU-BK, `mn` is lowercase)

Run: uv run pytest
"""

import pytest

import ocr


def one(text, **kw):
    out = ocr.parse_text(text, **kw)
    assert len(out["rows"]) == 1, out
    return out["rows"][0]


# -- README.md:18-20, the example block --------------------------------------
@pytest.mark.parametrize("line,sku,qty", [
    ("PHC-MIN-MP-WLB X3        MT-161     1", "PHC-MIN-MP-WLB", 3),   # README.md:18
    ("PHC-GCL-DW-WLB X2        MT-162    2", "PHC-GCL-DW-WLB", 2),    # README.md:19
    ("PHC-ELE-BK X1            MT-163    1", "PHC-ELE-BK", 1),        # README.md:20
])
def test_example_block_lines(line, sku, qty):
    r = one(line)
    assert (r["sku"], r["qty"], r["qty_detected"]) == (sku, qty, True)


def test_example_block_is_three_rows_and_nothing_unparsed():
    block = ("PHC-MIN-MP-WLB X3        MT-161     1\n"
             "PHC-GCL-DW-WLB X2        MT-162    2\n"
             "PHC-ELE-BK X1            MT-163    1\n")
    out = ocr.parse_text(block)
    assert [(r["sku"], r["qty"]) for r in out["rows"]] == [
        ("PHC-MIN-MP-WLB", 3), ("PHC-GCL-DW-WLB", 2), ("PHC-ELE-BK", 1)]
    assert out["unparsed"] == []


# -- README.md:22, one row per notation ---------------------------------------
@pytest.mark.parametrize("line,qty", [
    ("PHC-ELE-BK X3", 3),        # X3
    ("PHC-ELE-BK x3", 3),        # x3
    ("PHC-ELE-BK ×3", 3),        # ×3
    ("PHC-ELE-BK 3X", 3),        # 3X
    ("3x PHC-ELE-BK", 3),        # "3X" with the quantity first (ocr.parse_text docstring: "3x SKU")
    ("PHC-ELE-BK (3)", 3),       # (3)
    ("PHC-ELE-BK 4", 4),         # 纯数字
    ("PHC-ELE-BKX7", 7),         # 紧贴写法
])
def test_quantity_notations(line, qty):
    r = one(line)
    assert r["sku"] == "PHC-ELE-BK"
    assert (r["qty"], r["qty_detected"]) == (qty, True)


# -- README.md:23 ---------------------------------------------------------------
def test_excel_row_description_sku_quantity():
    r = one("Phone case\tPHC-ELE-BK\t5")
    assert (r["sku"], r["qty"], r["qty_detected"]) == ("PHC-ELE-BK", 5, True)


def test_no_quantity_means_one_and_is_flagged_as_not_read():
    r = one("PHC-ELE-BK")
    assert r["qty"] == 1                  # "不写数量按 1 张算"
    assert r["qty_detected"] is False     # "并标黄": the UI yellows rows whose quantity was not read


# -- README.md:24 ---------------------------------------------------------------
def test_unrecognised_line_is_listed_not_dropped():
    out = ocr.parse_text("hello world\nPHC-ELE-BK X2")
    assert [r["sku"] for r in out["rows"]] == ["PHC-ELE-BK"]
    assert out["unparsed"] == ["hello world"]


def test_blank_input_gives_nothing():
    assert ocr.parse_text("") == {"rows": [], "unparsed": []}
    assert ocr.parse_text("\n  \n") == {"rows": [], "unparsed": []}


# -- README.md:25-27, 「按原文」 ------------------------------------------------
def test_default_mode_cannot_keep_sc_comma_abc():
    # README.md:26: by default `SC,ABC` is cut at the comma and normalised into `SCABC`
    assert ocr.normalize_sku("SC,ABC", correct_ocr=False) == "SCABC"
    out = ocr.parse_text("SC,ABC")
    assert out["rows"] == [] and out["unparsed"] == ["SC,ABC"]


@pytest.mark.parametrize("line,sku,qty,detected", [
    ("SC,ABC", "SC,ABC", 1, False),          # README.md:26 "勾上就是 SC,ABC"
    ("ABC 3", "ABC 3", 1, False),            # README.md:27 "裸数字算正文的一部分"
    ("ABC X3", "ABC", 3, True),              # README.md:27 trailing X3
    ("ABC (3)", "ABC", 3, True),             # README.md:27 trailing (3)
    ("abc 中文 x2", "abc 中文", 2, True),     # README.md:25 "逗号/空格/小写/中文都保留"
])
def test_raw_mode(line, sku, qty, detected):
    r = one(line, raw=True)
    assert (r["sku"], r["qty"], r["qty_detected"]) == (sku, qty, detected)


# -- README.md:249 --------------------------------------------------------------
def test_sku_case_is_kept():
    r = one("UK4-SS-mn-GL-AU-BK 2")
    assert r["sku"] == "UK4-SS-mn-GL-AU-BK"
    assert ocr.normalize_sku("UK4-SS-mn-GL-AU-BK", correct_ocr=False) == "UK4-SS-mn-GL-AU-BK"


# -- parse_rows (OCR cells -> rows), README.md:28 and :245 -------------------------
def _box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def test_parse_rows_quantity_cell_and_low_score_yellow():
    items = [
        (_box(0, 0, 200, 30), "PHC-MIN-MP-WLB", 0.99), (_box(300, 0, 340, 30), "3", 0.99),   # sku + qty, sure
        (_box(0, 60, 200, 90), "PHC-ELE-BK", 0.30),                                          # 0.30 < min_score 0.85, no qty
        (_box(0, 120, 200, 150), "PHC-GCL-DW-WLB", 0.99), (_box(300, 120, 340, 150), "X2", 0.97),
    ]
    assert ocr.DEFAULT_MIN_SCORE == 0.85                      # README.md:245
    rows = ocr.parse_rows(items)
    got = [(r["sku"], r["qty"], r["qty_detected"], r["low_confidence"]) for r in rows]
    assert got == [
        ("PHC-MIN-MP-WLB", 3, True, False),
        ("PHC-ELE-BK", 1, False, True),       # 标黄: low confidence and quantity not read
        ("PHC-GCL-DW-WLB", 2, True, False),
    ]
