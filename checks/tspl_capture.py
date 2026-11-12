"""printing_tspl.encode_job 和 Windows 驱动的真实输出逐字节一致吗？

    uv run python checks/tspl_capture.py        # 退出码 0 = 全对，1 = 有不一致

判据来自代码之外：fixtures/*.prn 是 2026-11-11 在 office 上让 CT-428D 的 Windows 驱动
把作业写进文件截下来的（`StartDoc` 带输出文件，DEVMODE 与平时打印同一份），
fixtures/*.png 是同一次 label.render_label 渲染、交给驱动的那张图。

    cap60x30  SKU 标签 CAP-TEST-01，60×30 mm，2 张（验每张重发、作业头只发一次）
    cap90x50  SKU 标签 CAP-TEST-02，90×50 mm，1 张（验 719 点宽补白、BITMAP 切块）

自带一个已知为坏的样本：把作业头的 GAP 改掉后必须判不一致，否则这个检查本身作废
（退出码 2）。变异测试记录（2026-11-11）：
    - `_bitmap` 去掉补白（宽 719 直接 tobytes）→ cap90x50 不一致 ✔
    - `_CHUNK_BYTES` 改成 32768 → cap90x50 不一致 ✔
    - 每张改成 PRINT 1,N 合并 → cap60x30 不一致 ✔
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from PIL import Image  # noqa: E402

import printing_tspl  # noqa: E402

CASES = [("cap60x30", 60.0, 30.0, 2), ("cap90x50", 90.0, 50.0, 1)]


def _load(stem: str):
    img = Image.open(os.path.join(HERE, "fixtures", stem + ".png"))
    with open(os.path.join(HERE, "fixtures", stem + ".prn"), "rb") as f:
        return img, f.read()


def _first_diff(a: bytes, b: bytes) -> int:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b))


def main() -> int:
    bad = 0
    for stem, w, h, copies in CASES:
        img, want = _load(stem)
        got = printing_tspl.encode_job([(img, copies)], w, h)
        if got == want:
            print(f"OK    {stem}: {len(got)} 字节一致")
        else:
            i = _first_diff(got, want)
            print(f"FAIL  {stem}: 生成 {len(got)} / 截获 {len(want)} 字节，首个不同在 {i}："
                  f" 生成 {got[max(0, i - 8):i + 16]!r} / 截获 {want[max(0, i - 8):i + 16]!r}")
            bad += 1

    # 对照：已知为坏的样本必须被判出来
    img, want = _load("cap60x30")
    setup = printing_tspl._SETUP
    try:
        printing_tspl._SETUP = tuple(s.replace("GAP 3.48", "GAP 2.00") for s in setup)
        mutated = printing_tspl.encode_job([(img, 2)], 60.0, 30.0)
    finally:
        printing_tspl._SETUP = setup
    if mutated == want:
        print("CONTROL FAIL  改了 GAP 的样本也被判一致，这个检查不可信")
        return 2
    print("OK    对照：改了 GAP 的样本被判不一致")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
