"""label.barcode_info with module_px = FILL_WIDTH, against README.md:262 (条码没占满标签宽度).

The README says: fill mode uses a fractional module width to spread the barcode over the printable width, but a module
under 2 dots (measured with zxing-cpp: 1.8 and below unreadable, 1.85 and up stable) is refused and the layout falls
back to a whole number of dots, with the reason shown under the preview.

Expected numbers are worked by hand, not read from the code: 60 mm at 203 dpi is 480 dots; the default 3 mm margin is 24
dots a side, so 432 dots are printable; a Code128 symbol is 11 modules (start, one per character, check) plus a
13-module stop, so n characters make 11 * (n + 2) + 13 modules. 16 characters: 211 modules, 432 / 211 = 2.05 dots per
module, accepted. 17 characters: 222 modules, 432 / 222 = 1.95, refused.
"""

import pytest

import label

FILL = label.Layout(module_px=label.FILL_WIDTH)
PRINTABLE_DOTS = 432


def modules_for(chars):
    return 11 * (chars + 2) + 13


def info(chars, layout=FILL):
    return label.barcode_info("A" * chars, 60, layout)


def test_the_hand_count_of_modules_is_what_the_encoder_makes():
    assert len(label.code128_modules("A" * 16)) == modules_for(16) == 211
    assert len(label.code128_modules("A" * 17)) == modules_for(17) == 222


def test_two_dots_per_module_or_more_spreads_the_barcode_over_the_printable_width():
    got = info(16)
    assert got["fractional"] is True
    assert got["fill_rejected"] is False
    assert got["module_px"] == pytest.approx(PRINTABLE_DOTS / 211, abs=0.01)
    assert got["barcode_width_mm"] == pytest.approx(got["avail_width_mm"], abs=0.1)


def test_under_two_dots_per_module_falls_back_to_a_whole_number_and_says_so():
    got = info(17)
    assert got["fill_rejected"] is True
    assert got["fractional"] is False
    assert got["module_px"] == int(got["module_px"])
    assert got["module_px"] <= PRINTABLE_DOTS / 222
    assert got["overflow"] is False


def test_a_twenty_character_sku_on_60_mm_is_the_readme_example_of_the_fallback():
    got = info(20)
    assert got["fill_rejected"] is True
    assert got["module_px"] == int(got["module_px"])


def test_the_plain_automatic_layout_is_never_fractional():
    for chars in (3, 16, 17):
        assert info(chars, label.Layout())["fractional"] is False


def test_a_short_sku_fills_the_width_with_a_wide_module():
    got = info(3)
    assert got["fractional"] is True
    assert got["module_px"] == pytest.approx(PRINTABLE_DOTS / 68, abs=0.01)
