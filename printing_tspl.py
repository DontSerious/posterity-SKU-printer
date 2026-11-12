"""TSPL2 直写后端（Linux）：把 1:1 位图包成 TSPL 命令，写进 /dev/usb/lpN。

只在非 Windows 上被 printing.py 用到。Windows 那条路（GDI + 厂商驱动）不经过这里。

为什么不走 CUPS：CT-428D 在 Linux 上没有厂商驱动可装，而它自报的指令集就是
TSPL2（`/sys/class/usbmisc/lp0/device/ieee1284_id` 读出
`MFG:CTPrinter;CMD:TSPL2;MDL:CT-428D;`）。label.py 本来就按 203dpi 出 1:1 位图，
驱动在中间唯一做的事就是把它包成 TSPL —— 那这一层我们自己做，少一个会改像素的环节。

**下面每个常量都是照 Windows 驱动的真实输出抄的，不是查手册填的。** 2026-11-11 在
office 上用同一份 DEVMODE、`StartDoc(..., 输出文件)` 让驱动把作业写进文件（60×30 两张、
90×50 一张），截出来的字节就是：

    SET PEEL OFF / SET CUTTER OFF / SET TEAR ON / DIRECTION 1,0 / REFERENCE 0,0 /
    SPEED 5.00 / DENSITY 10 / OFFSET 0.00 mm / GAP 3.48 mm,0.0 mm      ← 作业开头一次
    SIZE 60.0 mm,30.0 mm / CLS / (空行) / BITMAP 0,0,60,240,1,<14400 字节> / PRINT 1,1
                                                                    ← 每张一遍
位图解出来和 label.py 渲染的 PNG 逐像素相同（115200/115200）。GAP 3.48 mm 是用户在驱动
首选项里校准过的纸间隙，改它就是改走纸定位，别动。`checks/tspl_capture.py` 拿这两份截获
做逐字节对照。
"""

from __future__ import annotations

import glob
import os
import threading

from PIL import Image

DPI = 203
# 驱动报的 HORZRES。CT-428D 打印头 833 点 = 104.2 mm，比这宽的纸永远印不上。
HEAD_DOTS = 833

_SETUP = (
    "SET PEEL OFF",
    "SET CUTTER OFF",
    "SET TEAR ON",
    "DIRECTION 1,0",
    "REFERENCE 0,0",
    "SPEED 5.00",
    "DENSITY 10",
    "OFFSET 0.00 mm",
    "GAP 3.48 mm,0.0 mm",  # 改这里要连 GAP_MM 一起改
)

# 驱动把一张图切成多条 BITMAP：90×50（每行 90 字节、400 行）切成 280 + 120 行。
# 25600 // 90 = 284，向下取到 8 的倍数 = 280，和截获一致；60×30 的 240 行不用切。
_CHUNK_BYTES = 25600

# 网页和 ERP 打印桥是两个线程，Windows 上 spooler 替我们排队，这里得自己排：
# 两个作业的字节交错写进同一个设备，打印机会把半张图当命令解析。
_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# 设备发现
# --------------------------------------------------------------------------- #

def _ieee1284(dev: str) -> dict:
    """读 usblp 设备的 IEEE 1284 ID，拆成 {"MFG": …, "CMD": …, "MDL": …}。"""
    path = f"/sys/class/usbmisc/{os.path.basename(dev)}/device/ieee1284_id"
    try:
        with open(path, encoding="ascii", errors="replace") as f:
            raw = f.read()
    except OSError:
        return {}
    out = {}
    for part in raw.strip().split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip().upper()] = v.strip()
    return out


def _devices() -> list[dict]:
    """说 TSPL 的 USB 打印机。别的打印机这里驱动不了，干脆不列出来。

    名字拼成「MFG MDL」，正好等于 Windows 驱动的队列名（`CTPrinter CT-428D`），
    所以从 office 搬来的 config.json 和 ERP 那边记住的打印机名都不用改。
    """
    out = []
    for dev in sorted(glob.glob("/dev/usb/lp*")):
        ident = _ieee1284(dev)
        cmds = [c.strip().upper() for c in ident.get("CMD", "").split(",")]
        if not any(c.startswith("TSPL") for c in cmds):
            continue
        name = " ".join(x for x in (ident.get("MFG"), ident.get("MDL")) if x) or dev
        out.append({"name": name, "port": dev, "driver": ident.get("CMD", "")})
    return out


def _device_of(printer: str) -> str:
    for d in _devices():
        if d["name"] == printer:
            return d["port"]
    return ""


def list_printers() -> list[dict]:
    devs = _devices()
    return [
        {"name": d["name"], "port": d["port"], "driver": d["driver"], "is_default": i == 0}
        for i, d in enumerate(devs)
    ]


def get_default_printer() -> str:
    devs = _devices()
    return devs[0]["name"] if devs else ""


def list_papers(printer: str) -> list[dict]:
    """没有驱动就没有驱动预设纸张；前端只剩快捷尺寸和自定义，够用。"""
    return []


def printer_resolution(printer: str) -> dict:
    if not _device_of(printer):
        return {"error": f"找不到打印机 {printer}"}
    return {"dpi_x": DPI, "dpi_y": DPI, "horz_res": HEAD_DOTS}


# --------------------------------------------------------------------------- #
# 编码与发送
# --------------------------------------------------------------------------- #

def canvas_px(width_mm: float, height_mm: float) -> tuple[int, int]:
    """一张标签的点阵尺寸，等于驱动给的 HORZRES × VERTRES（60×30 → 480×240）。"""
    w = max(1, int(round(width_mm / 25.4 * DPI)))
    h = max(1, int(round(height_mm / 25.4 * DPI)))
    return min(w, HEAD_DOTS), h


def _bitmap(img: Image.Image) -> tuple[int, int, bytes]:
    """→ (每行字节数, 行数, 位图)。位 1 = 不加热（白），和 PIL 的 "1" 模式同号。

    宽度补到 8 的倍数时补白，不然 PIL 用 0 补位，最右一列会印成一道黑线。
    灰度按 128 二值化，不抖动：PDF 栅格化缩放后边缘是灰的，抖动会把条码和
    二维码的边缘打成麻点。label.py 出的标签本来就只有纯黑纯白，不受影响。
    """
    g = img.convert("L")
    w, h = g.size
    wbytes = (w + 7) // 8
    if wbytes * 8 != w:
        padded = Image.new("L", (wbytes * 8, h), 255)
        padded.paste(g, (0, 0))
        g = padded
    bw = g.point(lambda v: 255 if v >= 128 else 0).convert("1", dither=Image.Dither.NONE)
    return wbytes, h, bw.tobytes()


def encode_job(pages: list[tuple[Image.Image, int]], width_mm: float, height_mm: float) -> bytes:
    """pages: [(整张标签的位图, 张数), …] → 一个完整作业的 TSPL 字节。纯函数。

    每张都重发一遍位图、各自 PRINT 1,1，照驱动的做法；不用 PRINT 1,N 合并。
    """
    out = bytearray("".join(line + "\r\n" for line in _SETUP).encode("ascii"))
    size = f"SIZE {width_mm:.1f} mm,{height_mm:.1f} mm\r\nCLS\r\n\r\n".encode("ascii")
    for img, copies in pages:
        wbytes, h, data = _bitmap(img)
        rows = max(8, (_CHUNK_BYTES // wbytes) // 8 * 8)
        page = bytearray(size)
        for y in range(0, h, rows):
            n = min(rows, h - y)
            page += f"BITMAP 0,{y},{wbytes},{n},1,".encode("ascii")
            page += data[y * wbytes:(y + n) * wbytes]
            page += b"\r\n"
        page += b"PRINT 1,1\r\n"
        for _ in range(max(1, int(copies))):
            out += page
    return bytes(out)


# 纸间隙，和 _SETUP 里的 GAP 同一个数。回退一张 = 标签高 + 间隙。
GAP_MM = 3.48


def encode_backfeed(width_mm: float, height_mm: float) -> tuple[bytes, int]:
    """往回退一张纸（标签高 + 间隙）的 TSPL 字节，和退了多少点。纯函数。

    给装纸用的：合上打印头后机器会走一张空白标签去找间隙，那张就挂在撕纸口外面
    白白浪费。退回一个标签间距，它回到打印头下面，下一次打印就印在它上面。
    先发 SIZE / GAP，打印机才知道这一卷的间距；BACKFEED 的单位是点（203dpi）。
    """
    dots = int(round((height_mm + GAP_MM) / 25.4 * DPI))
    lines = list(_SETUP) + [f"SIZE {width_mm:.1f} mm,{height_mm:.1f} mm", f"BACKFEED {dots}"]
    return "".join(line + "\r\n" for line in lines).encode("ascii"), dots


def backfeed(printer: str, width_mm: float, height_mm: float) -> int:
    """回退一张纸，返回退了多少点。"""
    data, dots = encode_backfeed(width_mm, height_mm)
    _write(printer, data)
    return dots


def send(printer: str, pages: list[tuple[Image.Image, int]], width_mm: float, height_mm: float) -> int:
    """编码并写进设备，返回出了几张。写不进去就抛 OSError / RuntimeError，由调用方报给用户。"""
    _write(printer, encode_job(pages, width_mm, height_mm))
    return sum(max(1, int(c)) for _, c in pages)


def _write(printer: str, data: bytes) -> None:
    dev = _device_of(printer)
    if not dev:
        raise RuntimeError(f"找不到打印机 {printer}（/dev/usb/lp* 里没有它，USB 线插好了吗）")
    with _lock:
        try:
            fd = os.open(dev, os.O_WRONLY)
        except PermissionError as exc:
            raise RuntimeError(f"没有权限写 {dev}：服务用户要在 lp 组里") from exc
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
        finally:
            os.close(fd)
