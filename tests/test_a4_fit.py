# -*- coding: utf-8 -*-
"""printing_cups.a4_fit_page: an order-table screenshot laid out on one portrait A4 page.

Expected numbers are worked by hand from A4 = 210 x 297 mm and a 10 mm margin
(at 100 dpi: page 827 x 1169 px, margin 39 px, usable 749 x 1091 px), not by
calling the code.

Run: uv run pytest
"""

from PIL import Image

import printing_cups


def black(w, h):
    return Image.new("L", (w, h), 0)


def bbox(page):
    # Ink box on the white page: invert so black content becomes non-zero
    return Image.eval(page.convert("L"), lambda v: 255 - v).getbbox()


def test_page_is_a4_portrait_at_given_dpi():
    page = printing_cups.a4_fit_page(black(100, 50), dpi=100)
    assert page.size == (827, 1169)


def test_wide_screenshot_fills_usable_width_at_top():
    # 1000 x 500 -> scale 749/1000 -> 749 x 374 (or 375), pinned to the top margin, centred
    page = printing_cups.a4_fit_page(black(1000, 500), dpi=100)
    x0, y0, x1, y1 = bbox(page)
    assert (x0, y0, x1) == (39, 39, 39 + 749)
    assert abs((y1 - y0) - 374.5) <= 1


def test_tall_screenshot_limited_by_usable_height():
    # 500 x 2000 -> scale 1091/2000 -> 272.75 x 1091, centred horizontally
    page = printing_cups.a4_fit_page(black(500, 2000), dpi=100)
    x0, y0, x1, y1 = bbox(page)
    assert (y0, y1) == (39, 39 + 1091)
    assert abs((x1 - x0) - 272.75) <= 1
    assert abs((x0 + x1) / 2 - 827 / 2) <= 1


def test_transparent_png_prints_white_not_black():
    img = Image.new("RGBA", (100, 50), (0, 0, 0, 0))   # fully transparent, RGB underneath is black
    page = printing_cups.a4_fit_page(img, dpi=100)
    assert page.convert("L").getextrema() == (255, 255)


def test_small_screenshot_is_enlarged_to_width():
    # A 300 px phone crop must not print stamp-sized: it is scaled up to the usable width too
    page = printing_cups.a4_fit_page(black(300, 100), dpi=100)
    x0, _, x1, _ = bbox(page)
    assert (x0, x1) == (39, 39 + 749)
