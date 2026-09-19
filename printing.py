"""打印机枚举与 GDI 打印。

只有 print_labels() 会真正出纸；其余函数都是只读查询。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from PIL import ImageWin

import win32con
import win32gui
import win32print
import win32ui

from label import Layout, render_label

DMPAPER_USER = 256


@dataclass
class PaperSpec:
    """一次打印用的纸张定义。paper_id=256 表示自定义尺寸。"""

    width_mm: float = 60.0
    height_mm: float = 30.0
    paper_id: int = DMPAPER_USER


# --------------------------------------------------------------------------- #
# 只读查询
# --------------------------------------------------------------------------- #

def list_printers() -> list[dict]:
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    default = get_default_printer()
    out = []
    for p in win32print.EnumPrinters(flags, None, 2):
        name = p["pPrinterName"]
        out.append(
            {
                "name": name,
                "port": p.get("pPortName") or "",
                "driver": p.get("pDriverName") or "",
                "is_default": name == default,
            }
        )
    out.sort(key=lambda x: (not x["is_default"], x["name"].lower()))
    return out


def get_default_printer() -> str:
    try:
        return win32print.GetDefaultPrinter()
    except Exception:
        return ""


def list_papers(printer: str) -> list[dict]:
    """从驱动读该打印机支持的纸张（名称 / DMPAPER id / 尺寸）。"""
    port = _port_of(printer)
    try:
        names = win32print.DeviceCapabilities(printer, port, win32con.DC_PAPERNAMES)
        ids = win32print.DeviceCapabilities(printer, port, win32con.DC_PAPERS)
        sizes = win32print.DeviceCapabilities(printer, port, win32con.DC_PAPERSIZE)
    except Exception:
        return []

    papers = []
    for i, name in enumerate(names or []):
        pid = ids[i] if ids and i < len(ids) else 0
        w_mm = h_mm = 0.0
        if sizes and i < len(sizes):
            # pywin32 把 DC_PAPERSIZE 的每项返回成 {'x':…, 'y':…}，单位 0.1mm
            sz = sizes[i]
            sx, sy = (sz["x"], sz["y"]) if isinstance(sz, dict) else (sz[0], sz[1])
            w_mm = round(sx / 10.0, 1)
            h_mm = round(sy / 10.0, 1)
        papers.append(
            {
                "name": str(name).strip("\x00").strip(),
                "id": int(pid),
                "width_mm": w_mm,
                "height_mm": h_mm,
            }
        )
    return papers


def printer_resolution(printer: str) -> dict:
    """返回打印机 dpi 与可打印区域点数，用于诊断。"""
    try:
        hdc = win32gui.CreateDC("WINSPOOL", printer, None)
        dc = win32ui.CreateDCFromHandle(hdc)
        info = {
            "dpi_x": dc.GetDeviceCaps(win32con.LOGPIXELSX),
            "dpi_y": dc.GetDeviceCaps(win32con.LOGPIXELSY),
            "horz_res": dc.GetDeviceCaps(win32con.HORZRES),
            "vert_res": dc.GetDeviceCaps(win32con.VERTRES),
        }
        dc.DeleteDC()
        return info
    except Exception as exc:
        return {"error": str(exc)}


def _port_of(printer: str) -> str:
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    for p in win32print.EnumPrinters(flags, None, 2):
        if p["pPrinterName"] == printer:
            return p.get("pPortName") or ""
    return ""


# --------------------------------------------------------------------------- #
# 打印
# --------------------------------------------------------------------------- #

def _make_dc(printer: str, paper: PaperSpec):
    """按指定纸张尺寸建打印 DC。60×30 不在驱动预设里，走 DMPAPER_USER。"""
    h = win32print.OpenPrinter(printer)
    try:
        dm = win32print.GetPrinter(h, 2)["pDevMode"]
    finally:
        win32print.ClosePrinter(h)

    if dm is None:
        raise RuntimeError(f"打印机 {printer} 未返回 DEVMODE，无法设置纸张尺寸")

    dm.Orientation = win32con.DMORIENT_PORTRAIT
    dm.PaperSize = int(paper.paper_id)
    fields = dm.Fields | win32con.DM_ORIENTATION | win32con.DM_PAPERSIZE

    if int(paper.paper_id) == DMPAPER_USER:
        # DEVMODE 里长度单位是 0.1mm
        dm.PaperWidth = int(round(paper.width_mm * 10))
        dm.PaperLength = int(round(paper.height_mm * 10))
        fields |= win32con.DM_PAPERWIDTH | win32con.DM_PAPERLENGTH

    dm.Fields = fields

    hdc = win32gui.CreateDC("WINSPOOL", printer, dm)
    return win32ui.CreateDCFromHandle(hdc)


def print_labels(
    printer: str,
    rows: list[dict],
    paper: PaperSpec,
    layout: Layout | dict | None = None,
    job_name: str = "SKU Labels",
) -> dict:
    """rows: [{"sku": "EU-PRO-01", "qty": 3}, ...]，每行按 qty 出 qty 张相同标签。"""
    if isinstance(layout, dict) or layout is None:
        layout = Layout.from_dict(layout)

    jobs = [(r["sku"], max(1, int(r.get("qty", 1)))) for r in rows if r.get("sku")]
    if not jobs:
        return {"ok": False, "error": "没有可打印的行", "printed": 0}

    dc = _make_dc(printer, paper)
    try:
        w_px = dc.GetDeviceCaps(win32con.HORZRES)
        h_px = dc.GetDeviceCaps(win32con.VERTRES)

        dc.StartDoc(job_name)
        printed = 0
        try:
            for sku, qty in jobs:
                img = render_label(
                    sku, paper.width_mm, paper.height_mm, layout, canvas_px=(w_px, h_px)
                )
                dib = ImageWin.Dib(img.convert("RGB"))
                for _ in range(qty):
                    # 逐页出，不用 DEVMODE.Copies —— 热敏驱动经常忽略该字段
                    dc.StartPage()
                    dib.draw(dc.GetHandleOutput(), (0, 0, w_px, h_px))
                    dc.EndPage()
                    printed += 1
        finally:
            dc.EndDoc()
    finally:
        dc.DeleteDC()

    return {
        "ok": True,
        "printed": printed,
        "canvas_px": [w_px, h_px],
        "skus": [s for s, _ in jobs],
    }


def fit_to_canvas(img, w_px: int, h_px: int):
    """把一页放到 w_px × h_px 的白底画布上：方向不一致先转 90°，再等比缩放居中。

    等比而不是拉伸，是因为纸张可以和页面不同形状（正方形的快递面单打到
    60 × 30 的纸上），拉伸会把条码的模块宽度压歪。页面和纸张同比例时结果
    就是铺满整页，和以前一样。
    """
    from PIL import Image

    if (img.width >= img.height) != (w_px >= h_px):
        img = img.rotate(90, expand=True)
    scale = min(w_px / img.width, h_px / img.height)
    tw = max(1, int(round(img.width * scale)))
    th = max(1, int(round(img.height * scale)))
    page = img.convert("RGB").resize((tw, th), Image.LANCZOS)
    canvas = Image.new("RGB", (w_px, h_px), "white")
    canvas.paste(page, ((w_px - tw) // 2, (h_px - th) // 2))
    return canvas


def print_images(
    printer: str,
    images: list,
    paper: PaperSpec,
    copies: int = 1,
    job_name: str = "Label file",
) -> dict:
    """打印现成的图片（PDF 每页一张 / 上传的 PNG），等比缩放居中到整页。

    与 print_labels 的区别：不渲染条码，图片是外部生成的（比如 ERP 导出的
    二维码标签 PDF）。页面方向与纸张不一致时自动旋转 90°，所以横版 PDF
    打竖装的纸也不会被压扁；纸张比例和页面不同时留白，不拉伸。
    """
    if not images:
        return {"ok": False, "error": "没有可打印的页面", "printed": 0}

    dc = _make_dc(printer, paper)
    try:
        w_px = dc.GetDeviceCaps(win32con.HORZRES)
        h_px = dc.GetDeviceCaps(win32con.VERTRES)

        dc.StartDoc(job_name)
        printed = 0
        try:
            for img in images:
                dib = ImageWin.Dib(fit_to_canvas(img, w_px, h_px))
                for _ in range(max(1, int(copies))):
                    dc.StartPage()
                    dib.draw(dc.GetHandleOutput(), (0, 0, w_px, h_px))
                    dc.EndPage()
                    printed += 1
        finally:
            dc.EndDoc()
    finally:
        dc.DeleteDC()

    return {"ok": True, "printed": printed, "canvas_px": [w_px, h_px]}


def dry_run_images(
    images: list,
    copies: int = 1,
    out_dir: str = "output",
    stem: str = "file",
    paper: PaperSpec | None = None,
    dpi: int = 203,
) -> dict:
    """干跑：把每页存成 PNG，不出纸。

    给了 paper 就先按纸张尺寸合成（和真打一样等比缩放居中），PNG 看到的就是
    出纸的样子；不给则原样存页面。
    """
    os.makedirs(out_dir, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem) or "file"
    if paper is not None:
        w_px = max(1, int(round(paper.width_mm / 25.4 * dpi)))
        h_px = max(1, int(round(paper.height_mm / 25.4 * dpi)))
        images = [fit_to_canvas(img, w_px, h_px) for img in images]
    files = []
    for i, img in enumerate(images, 1):
        path = os.path.join(out_dir, f"{safe}_p{i}.png")
        img.convert("L").save(path)
        files.append(os.path.abspath(path))
    return {"ok": True, "printed": 0, "dry_run": True,
            "total_labels": len(images) * max(1, int(copies)), "files": files}


def dry_run_labels(
    rows: list[dict],
    paper: PaperSpec,
    layout: Layout | dict | None = None,
    out_dir: str = "output",
) -> dict:
    """干跑：只把标签存成 PNG，不出纸。用来核对版式，省标签纸。"""
    os.makedirs(out_dir, exist_ok=True)
    files = []
    total = 0
    for r in rows:
        sku = r.get("sku")
        if not sku:
            continue
        qty = max(1, int(r.get("qty", 1)))
        img = render_label(sku, paper.width_mm, paper.height_mm, layout)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in sku)
        path = os.path.join(out_dir, f"{safe}_x{qty}.png")
        img.convert("L").save(path)
        files.append(os.path.abspath(path))
        total += qty
    return {"ok": True, "printed": 0, "dry_run": True, "total_labels": total, "files": files}
