"""CUPS 后端（Linux）：给 A4 普通打印机用（gw 上的 EPSON L1350，走无线）。

只在非 Windows 上被 printing.py 用到。标签机（CT-428D）不走这里，走 printing_tspl.py
直写 —— 两条路并存，按打印机名分派：名字是 CUPS 队列名的走这里，其余走 TSPL。

两种打印，都**不传任何缩放选项**。CUPS 文档（cups.org/doc/options.html「Scaling to Fit」）
原话是 "The default is to use the size specified in the file."：
- SKU 标签：还是 label.py 那张 203dpi 的图（和网页预览同一张），这里把它贴在一张
  203dpi 的 A4 白底正中，存成页面正好是 A4 的 PDF。于是 60×30 mm 的标签在纸上就是
  60×30 mm，一张标签一页。`ppi` / `print-scaling` 这两个选项的语义在 CUPS 文档里查不到
  原文（2026-11-11 查过），所以不靠它们，尺寸由 PDF 本身钉死。
- 打印文件：上传的 PDF / 图片原样交给 CUPS，按文件自身的尺寸印在打印机的默认纸（A4）上。
  比 A4 大的页面 CUPS 怎么处理没有核过。

用 `lp` / `lpstat` 命令行而不是 pycups：少一个要编译的依赖，CUPS 自带这两个命令。
"""

from __future__ import annotations

import os
import subprocess
import tempfile

from PIL import Image

_TIMEOUT = 15
A4_MM = (210.0, 297.0)


def _run(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True, timeout=_TIMEOUT,
                          check=True).stdout


def queues() -> list[dict]:
    """CUPS 里配好的队列。没装 CUPS、或 cupsd 没在跑，就当一台都没有。"""
    try:
        out = _run(["lpstat", "-v"])
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.splitlines():
        # "device for EPSON-L1350: ipp://192.168.5.15/ipp/print"
        if line.startswith("device for ") and ": " in line:
            name, uri = line[len("device for "):].split(": ", 1)
            found.append({"name": name.strip(), "port": uri.strip(), "driver": "CUPS"})
    return found


def is_queue(printer: str) -> bool:
    return any(q["name"] == printer for q in queues())


def _lp(printer: str, path: str, copies: int, job_name: str) -> str:
    args = ["lp", "-d", printer, "-n", str(max(1, int(copies))), "-t", job_name, path]
    try:
        return _run(args).strip()  # "request id is EPSON-L1350-12 (1 file(s))"
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"lp 失败：{(exc.stderr or exc.stdout or '').strip()}") from exc


def a4_pages(pages: list, dpi: int) -> list[Image.Image]:
    """[(标签图, 张数), …] → 一张一页的 A4 白底页面，标签按 dpi 换算的实际尺寸居中。纯函数。"""
    pw = int(round(A4_MM[0] / 25.4 * dpi))
    ph = int(round(A4_MM[1] / 25.4 * dpi))
    out = []
    for img, copies in pages:
        g = img.convert("L")
        if g.width > pw or g.height > ph:  # 比 A4 还大的标签：等比缩进来，别裁掉
            s = min(pw / g.width, ph / g.height)
            g = g.resize((max(1, int(g.width * s)), max(1, int(g.height * s))), Image.NEAREST)
        page = Image.new("L", (pw, ph), 255)
        page.paste(g, ((pw - g.width) // 2, (ph - g.height) // 2))
        out.extend([page] * max(1, int(copies)))
    return out


def print_images(printer: str, pages: list, dpi: int, job_name: str) -> int:
    """pages: [(PIL 图, 张数), …]，合成一个 A4 PDF 一次交给 CUPS。返回张数。"""
    sheets = a4_pages(pages, dpi)
    with tempfile.TemporaryDirectory(prefix="sku-cups-") as tmp:
        path = os.path.join(tmp, "labels.pdf")
        # resolution 决定 PDF 的页面尺寸：像素 / dpi = 英寸，于是页面正好是 A4
        sheets[0].save(path, "PDF", resolution=float(dpi), save_all=True,
                       append_images=sheets[1:])
        _lp(printer, path, 1, job_name)
    return len(sheets)


def print_file(printer: str, data: bytes, filename: str, copies: int, job_name: str) -> str:
    """上传的文件原样交给 CUPS。返回 lp 的回执（含作业号）。"""
    ext = os.path.splitext(filename or "")[1].lower() or ".pdf"
    with tempfile.TemporaryDirectory(prefix="sku-cups-") as tmp:
        path = os.path.join(tmp, "upload" + ext)
        with open(path, "wb") as f:
            f.write(data)
        return _lp(printer, path, copies, job_name)
