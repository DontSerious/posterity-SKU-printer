"""标签位图渲染。

核心原则：按打印机的真实分辨率（CT-428D = 203dpi）生成 1:1 像素位图，
每个条码模块占整数个热敏点，绝不交给驱动做缩放 —— 否则窄条被重采样成
灰边，扫码枪读不出来。
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, asdict

from PIL import Image, ImageDraw, ImageFont

DPI = 203  # CT-428D 打印头分辨率，8 dots/mm

# Code128 规范要求左右各至少 10 个模块的静区
QUIET_ZONE_MODULES = 10

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\consolab.ttf",  # Consolas Bold：等宽 + 粗，热敏印出来最清晰
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
]


@dataclass
class Layout:
    """版式参数，全部用「占标签宽/高的百分比」表示，换纸张尺寸时自动等比。"""

    rotate: int = 0                  # 0 / 90 / 180 / 270
    margin_x_mm: float = 3.0         # 左右各留白，绝对毫米（换纸张尺寸不跟着缩）
    barcode_top_pct: float = 8.5     # 条码距顶部
    barcode_height_pct: float = 67.0 # 条码高度
    text_height_pct: float = 12.0    # SKU 文字高度
    module_px: int = 0               # 0 = 自动选最大可用整数模块宽
    show_text: bool = True

    @classmethod
    def from_dict(cls, d: dict | None) -> "Layout":
        d = d or {}
        known = {f: d[f] for f in cls.__dataclass_fields__ if f in d}
        return cls(**known)

    def to_dict(self) -> dict:
        return asdict(self)


def mm_to_px(mm: float, dpi: int = DPI) -> int:
    return max(1, int(round(mm / 25.4 * dpi)))


# --------------------------------------------------------------------------- #
# Code128 编码
# --------------------------------------------------------------------------- #

def code128_modules(data: str) -> str:
    """返回 Code128 的模块串，'1' = 黑，'0' = 白。"""
    from barcode import Code128

    built = Code128(data).build()
    if not built:
        raise ValueError(f"无法为 {data!r} 生成 Code128")
    modules = built[0]
    if set(modules) - {"0", "1"}:
        raise ValueError("python-barcode 返回了非预期的编码格式")
    return modules


# --------------------------------------------------------------------------- #
# 字体
# --------------------------------------------------------------------------- #

def _auto_module(n_modules: int, canvas_w: int, avail_w: int) -> int:
    """选能铺满可用宽度的最大整数模块宽。

    先按 avail_w 铺满，再回退到静区够用为止 —— Code128 要求条码两侧各留
    至少 10 个模块的空白，模块越宽，需要的静区也越宽。
    """
    m = max(1, avail_w // n_modules)
    while m > 1 and (canvas_w - n_modules * m) / 2 < QUIET_ZONE_MODULES * m:
        m -= 1
    return m


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _fit_font(draw: ImageDraw.ImageDraw, text: str, max_w: int, max_h: int):
    """二分找出宽高都放得下的最大字号，长 SKU 会自动缩小而不溢出。"""
    lo, hi, best = 6, max(8, max_h * 2), None
    while lo <= hi:
        mid = (lo + hi) // 2
        font = _load_font(mid)
        l, t, r, b = draw.textbbox((0, 0), text, font=font)
        if (r - l) <= max_w and (b - t) <= max_h:
            best = (font, r - l, b - t, l, t)
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None:
        font = _load_font(6)
        l, t, r, b = draw.textbbox((0, 0), text, font=font)
        best = (font, r - l, b - t, l, t)
    return best


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #

def _render_content(sku: str, w: int, h: int, layout: Layout) -> Image.Image:
    img = Image.new("1", (w, h), 1)  # 1 = 白
    draw = ImageDraw.Draw(img)

    modules = code128_modules(sku)
    n = len(modules)

    margin_x = mm_to_px(layout.margin_x_mm) if layout.margin_x_mm > 0 else 0
    avail_w = max(1, w - 2 * margin_x)

    module_px = layout.module_px if layout.module_px and layout.module_px > 0 else _auto_module(n, w, avail_w)
    if n * module_px > avail_w:  # 手动指定过大时收回来
        module_px = max(1, avail_w // n)

    bar_w = n * module_px
    x0 = (w - bar_w) // 2
    y0 = int(round(h * layout.barcode_top_pct / 100.0))
    bar_h = max(1, int(round(h * layout.barcode_height_pct / 100.0)))
    if y0 + bar_h > h:
        bar_h = max(1, h - y0)

    # 逐条画黑模块（把连续的 '1' 合并成一个矩形，减少绘制次数）
    i = 0
    while i < n:
        if modules[i] == "1":
            j = i
            while j < n and modules[j] == "1":
                j += 1
            draw.rectangle(
                [x0 + i * module_px, y0, x0 + j * module_px - 1, y0 + bar_h - 1],
                fill=0,
            )
            i = j
        else:
            i += 1

    if layout.show_text and sku:
        gap = int(round(h * 0.02))
        text_top = y0 + bar_h + gap
        text_h = max(1, int(round(h * layout.text_height_pct / 100.0)))
        if text_top + text_h > h:
            text_h = max(1, h - text_top)
        if text_h > 4:
            font, tw, th, off_l, off_t = _fit_font(draw, sku, avail_w, text_h)
            tx = (w - tw) // 2 - off_l
            ty = text_top + (text_h - th) // 2 - off_t
            draw.text((tx, ty), sku, font=font, fill=0)

    return img


def render_label(
    sku: str,
    width_mm: float = 60.0,
    height_mm: float = 30.0,
    layout: Layout | dict | None = None,
    canvas_px: tuple[int, int] | None = None,
) -> Image.Image:
    """渲染一张标签。

    canvas_px 传入时（打印路径会传驱动报的 PHYSICALWIDTH/HEIGHT），
    直接按设备实际点数出图，避免右边被裁；否则按标称 mm × 203dpi 计算。
    """
    if isinstance(layout, dict) or layout is None:
        layout = Layout.from_dict(layout)

    if canvas_px:
        cw, ch = canvas_px
    else:
        cw, ch = mm_to_px(width_mm), mm_to_px(height_mm)

    rotate = int(layout.rotate) % 360
    if rotate in (90, 270):
        content = _render_content(sku, ch, cw, layout)
        return content.rotate(rotate, expand=True)
    if rotate == 180:
        return _render_content(sku, cw, ch, layout).rotate(180, expand=False)
    return _render_content(sku, cw, ch, layout)


def render_png(sku: str, width_mm: float, height_mm: float, layout=None) -> bytes:
    """给网页预览用：和打印走同一个 render_label()，所见即所得。"""
    img = render_label(sku, width_mm, height_mm, layout)
    buf = io.BytesIO()
    img.convert("L").save(buf, format="PNG")
    return buf.getvalue()


def barcode_info(sku: str, width_mm: float, layout: Layout | dict | None = None) -> dict:
    """给前端提示用：模块数、实际模块宽、条码占用宽度(mm)。"""
    if isinstance(layout, dict) or layout is None:
        layout = Layout.from_dict(layout)
    n = len(code128_modules(sku))
    w = mm_to_px(width_mm)
    margin_x = mm_to_px(layout.margin_x_mm) if layout.margin_x_mm > 0 else 0
    avail_w = max(1, w - 2 * margin_x)
    m = layout.module_px if layout.module_px and layout.module_px > 0 else _auto_module(n, w, avail_w)
    if n * m > avail_w:
        m = max(1, avail_w // n)
    return {
        "modules": n,
        "module_px": m,
        "barcode_width_mm": round(n * m / DPI * 25.4, 2),
        "avail_width_mm": round(avail_w / DPI * 25.4, 2),
        "actual_margin_mm": round((w - n * m) / 2 / DPI * 25.4, 2),
        "fill_pct": round(n * m / w * 100, 1),
        "overflow": n * m > avail_w,
        "quiet_zone_ok": (w - n * m) / 2 >= QUIET_ZONE_MODULES * m,
        # 1 个模块 = 1 个热敏点 = 0.125mm，窄条已到打印头极限，部分扫码枪会读不稳
        "narrow": m <= 1,
    }
