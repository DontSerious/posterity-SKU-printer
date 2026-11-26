"""订单表格截图 → 结构化行（SKU / 数量 / 描述）。

不依赖任何表格结构模型：拿 OCR 的文本框坐标，按 y 聚类还原表格行，
再在行内按 x 顺序判断哪个是 SKU、哪个是数量。截图里有 `SKU` 表头（装箱单）时，
改按表头认列，见 _find_header。
"""

from __future__ import annotations

import re
import threading

# 2~7 段，段间用 '-'：EU-PRO-01 / PHC-MIN-MP-WLB / SS-GLA-AUR-GLA-WLB-WH
# 大小写都收：SKU 是大小写敏感的（UK4-SS-mn-GL-AU-BK 里的 mn 就是小写）。
# 空格**不能**进这个正则 —— 否则描述列里的 "Wall Lamp" 也成了合法 SKU。
# 含空格的 SKU（MCH-SS V2）只认字典命中，见 learned.py 的合并裁决。
DEFAULT_SKU_REGEX = r"^[A-Za-z0-9]{2,6}(-[A-Za-z0-9]{1,6}){1,6}$"
DEFAULT_MIN_SCORE = 0.85

_engine = None
_engine_lock = threading.Lock()
_engine_error: str | None = None


# --------------------------------------------------------------------------- #
# 引擎
# --------------------------------------------------------------------------- #

def get_engine():
    """RapidOCR 首次初始化要几秒（并可能联网下载一次模型），做成单例。"""
    global _engine, _engine_error
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is None:
            try:
                from rapidocr import RapidOCR

                _engine = RapidOCR()
            except Exception as exc:  # noqa: BLE001
                _engine_error = str(exc)
                raise
    return _engine


def engine_error() -> str | None:
    return _engine_error


def _raw_items(result) -> list[tuple[list, str, float]]:
    """兼容 rapidocr 3.x 的结果对象和 1.x 的 (list, elapse) 元组。"""
    if result is None:
        return []

    boxes = getattr(result, "boxes", None)
    txts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    if boxes is not None and txts is not None:
        items = []
        for i, txt in enumerate(txts):
            box = boxes[i]
            score = float(scores[i]) if scores is not None and i < len(scores) else 1.0
            items.append(([[float(p[0]), float(p[1])] for p in box], str(txt), score))
        return items

    # 1.x: (   [[box, text, score], ...], elapse )
    seq = result[0] if isinstance(result, tuple) else result
    items = []
    for entry in seq or []:
        box, txt, score = entry[0], entry[1], entry[2]
        items.append(([[float(p[0]), float(p[1])] for p in box], str(txt), float(score)))
    return items


# --------------------------------------------------------------------------- #
# SKU 归一化
# --------------------------------------------------------------------------- #

_DASHES = "‐‑‒–—―−－·•_"

# OCR 常把西里尔/希腊/全角字符认成形近的拉丁字母，先统一成拉丁再谈别的。
# 大小写两套都要 —— 不再无条件 upper 之后，小写西里尔不会再被大写表兜住。
_HOMOGLYPHS = str.maketrans({
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X", "І": "I", "Ј": "J",
    "З": "3", "Ѕ": "S", "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H",
    "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T",
    "Υ": "Y", "Χ": "X",
    "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "о": "o", "р": "p",
    "с": "c", "т": "t", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s",
    "α": "a", "β": "b", "ε": "e", "ι": "i", "κ": "k", "μ": "m", "ν": "v",
    "ο": "o", "ρ": "p", "τ": "t", "υ": "u", "χ": "x",
})
_ALPHA_TO_DIGIT = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1",
                                 "S": "5", "B": "8", "Z": "2", "G": "6", "T": "7"})


def normalize_sku(text: str, correct_ocr: bool = True) -> str:
    """清洗 SKU：统一破折号、去空格。**大小写原样保留**。

    SKU 是大小写敏感的（`UK4-SS-mn-GL-AU-BK` 里的 `mn` 就是小写），而 OCR
    本来就认得出来 —— 从前那句 `.upper()` 是在毁自己刚拿到的正确数据。

    correct_ocr=True（图片 OCR）时额外按段纠正易混字，但只往一个方向推、
    且必须有段内佐证，见下面的注释。粘贴文本是人敲的、没有形近错误，传 False。
    """
    s = text.strip()
    if correct_ocr:
        s = s.translate(_HOMOGLYPHS)  # 西里尔/希腊形近字，也只对 OCR 有意义
    for d in _DASHES:
        s = s.replace(d, "-")
    s = re.sub(r"[^\w\-]", "", s)
    s = s.replace(" ", "").strip("-")
    if not s:
        return ""
    if not correct_ocr:
        return s

    fixed = []
    for seg in s.split("-"):
        # ADR-0001
        as_digit = seg.translate(_ALPHA_TO_DIGIT)
        if as_digit.isdigit() and any(c.isdigit() for c in seg):
            fixed.append(as_digit)
        else:
            fixed.append(seg)
    return "-".join(fixed)


def _looks_like_sku(text: str, pattern: re.Pattern, correct_ocr: bool = True) -> str | None:
    # 乘号/星号永远不会出现在 SKU 里，别让 normalize 把它默默吃掉
    # （PHC-ELE-BK×5 若被吃掉会变成 PHC-ELE-BKS 这种假 SKU）
    if any(c in text for c in "×✕*"):
        return None
    cand = normalize_sku(text, correct_ocr=correct_ocr)
    if cand and pattern.match(cand):
        return cand
    return None


# --------------------------------------------------------------------------- #
# 表格还原
# --------------------------------------------------------------------------- #

_INT_RE = re.compile(r"^\d{1,5}$")


def _explode(box, txt: str, score: float) -> list[dict]:
    """一个 OCR 文本框 → 若干 token。

    OCR 经常把「描述 + SKU」甚至整行读进同一个框。若拿整框去匹配 SKU，
    轻则整行丢失，重则 "US-PRO-06 1" 被去掉空格后变成假 SKU "US-PRO-061"。
    所以先按空格拆成 token，按字符占比估算各自的 x 范围（用于列排序）。
    """
    text = txt.strip()
    if not text:
        return []
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    x0, x1, top, bot = min(xs), max(xs), min(ys), max(ys)

    parts = text.split()
    if len(parts) <= 1:
        parts_pos = [(text, 0, len(text))]
    else:
        parts_pos, cur = [], 0
        for p in parts:
            i = text.index(p, cur)
            cur = i + len(p)
            parts_pos.append((p, i, cur))

    span = max(1, len(text))
    width = x1 - x0
    cells = []
    for p, i, j in parts_pos:
        tx0 = x0 + width * i / span
        tx1 = x0 + width * j / span
        cells.append(
            {
                "text": p,
                "score": score,
                "x0": tx0,
                "x1": tx1,
                "cx": (tx0 + tx1) / 2,
                "top": top,
                "bot": bot,
                "cy": (top + bot) / 2,
                "h": max(1.0, bot - top),
            }
        )
    return cells


def _cluster_rows(cells: list[dict]) -> list[list[dict]]:
    """按纵向区间重叠聚类。

    判据是「互含中心点」：两个框各自的 y 中心都落在对方的纵向范围内才算同一行。
    比「中心点距离 < 阈值」稳 —— 后者会沿 y 逐个链式传染，行距小时把相邻行连成一片；
    也比单纯算重叠稳 —— 后者会让一个偏高的框把上下相邻行一起吞掉。
    每簇固定用第一个 token 当锚点比较，避免簇的纵向范围越滚越大。
    """
    rows: list[list[dict]] = []
    for c in sorted(cells, key=lambda c: (c["top"], c["x0"])):
        for row in rows:
            a = row[0]
            if a["top"] <= c["cy"] <= a["bot"] and c["top"] <= a["cy"] <= c["bot"]:
                row.append(c)
                break
        else:
            rows.append([c])
    for row in rows:
        row.sort(key=lambda c: c["cx"])
    return rows


_SKU_HEADER_RE = re.compile(r"^sku$", re.I)
_QTY_HEADER_RE = re.compile(r"^(qty|quantity|total|数量)$", re.I)


def _find_header(cells: list[dict]) -> dict | None:
    """找装箱单的表头：有一格就叫 `SKU` 才算有表头，没有就返回 None，走纯规则。

    装箱单（Packing list Sandsara Store）是 Description | SKU | Total | 每个发货区一列。
    没有表头时 parse_rows 只能拿「整行第一个像 SKU 的词」和「最右的整数」，在这种表上
    两条都错：数量读成最右那个发货区的分数，描述里的 `Sand- type`、`v1-` + `MT-01`
    被粘成像 SKU 的词抢在真 SKU 前面。

    表头带：SKU 那格上方 1.5 个字高到它的底边。两行的表头（`Qty` 在上、`Total` 在下，
    `USA` 在上、`AIR` 在下）上下两行都算进来；数量那一列的表头只认这条带里的格，
    下面合计行里的 `Total` 不算。

    叫 `SKU` 的格不止一个时（标题里写着 `SKU list …`），挑旁边有数量表头的那个；
    都没有就挑表头带里格最多的那个 —— 真表头那一行挤满了列名，标题行没几个词。
    """
    def build(sku):
        band = [c for c in cells if sku["top"] - 1.5 * sku["h"] <= c["cy"] <= sku["bot"]]
        qty = [c for c in band if _QTY_HEADER_RE.match(c["text"].strip()) and c["cx"] > sku["cx"]]
        right = [c["x0"] for c in band if c["x0"] > sku["x1"]]
        return {
            "sku": sku,
            "band": band,
            "bottom": max(c["bot"] for c in band),
            # `Qty` 与 `Total` 叠成两行、或挤在一格（`Qty Total`）时都是同一列，几格都留着
            "qty": qty,
            # SKU 列的右界：数量表头的左边；没读到数量表头，就是 SKU 表头右边最近那个列名的左边
            "right": min(h["x0"] for h in qty) if qty else min(right, default=None),
        }

    heads = [build(c) for c in cells if _SKU_HEADER_RE.match(c["text"].strip())]
    if not heads:
        return None
    return max(heads, key=lambda h: (bool(h["qty"]), len(h["band"]), -h["sku"]["top"]))


def _in_sku_column(c: dict, header: dict | None) -> bool:
    """这格落在 SKU 列里吗？没有表头时一律算在。

    左界是 SKU 表头的左边：表头左对齐时 SKU 也从那里起，居中时 SKU 比表头宽、右端更过界。
    Excel 会在列边界裁掉描述，所以描述格的右端到不了这里。右界见 _find_header 的 `right`。
    """
    if header is None:
        return True
    if c["x1"] <= header["sku"]["x0"]:
        return False
    return header["right"] is None or c["cx"] < header["right"]


def _qty_from_header(sku: dict, cells: list[dict], header: dict) -> int | None:
    """数量列里、和这个 SKU 同一行的那个整数。

    列：离这个数最近的表头格得是数量表头（发货区各列离它们自己的表头更近）。距离量到
    表头框的边、落在框里算 0，不量到中心 —— Excel 的筛选下拉箭头会被读成紧贴在 `Total`
    右边的一个 `T`，量中心的话数字离它和离 `Total` 一样近。打平算数量表头赢。

    行：不靠 _cluster_rows 的聚类，直接看纵向重叠：任一方的中心落在另一方的上下范围内就算
    （比 _cluster_rows 的「两方都要落在对方范围内」宽）。
    描述折成两行时，聚类的锚点是描述的上一行，SKU 跟它一簇，数量跟下一行描述一簇，
    数量就丢了。

    OCR 一个数量表头都没读到时（下拉箭头压在 `Total` 上，2026-10-27 那张就整格没了），
    取 SKU 右边最近的那个数：十张装箱单的 `Total` 都紧挨在 SKU 右边。
    """
    def gap(b, c):
        return max(b["x0"] - c["cx"], 0.0, c["cx"] - b["x1"])

    found = []
    for c in cells:
        val = _read_qty(c["text"].strip())
        if val is None or c["cx"] <= sku["x1"]:
            continue
        if not (sku["top"] <= c["cy"] <= sku["bot"] or c["top"] <= sku["cy"] <= c["bot"]):
            continue
        if header["qty"] and \
                min(gap(h, c) for h in header["qty"]) > min(gap(b, c) for b in header["band"]):
            continue
        found.append((val, c))
    if not found:
        return None
    if header["qty"]:
        return min(found, key=lambda f: abs(f[1]["cy"] - sku["cy"]))[0]
    return min(found, key=lambda f: f[1]["cx"])[0]


def _join_dangling_dash(row: list[dict], near_only: bool = False) -> list[dict]:
    """把被空格拆断的 SKU 粘回去：`SAN-NAT-` + `CRY` → `SAN-NAT-CRY`。

    OCR 把 `SAN-NAT- CRY` 整个读进一个框，_explode 按空格拆成两个 token，而
    `SAN-NAT-` 去掉尾连字符后**本身就是合法的 2 段 SKU** —— parse_rows 取第一个
    匹配就收工，`CRY` 被当右侧杂项扔了，打出来是一张看起来完全正常的错标签。

    以 '-' 结尾（或右邻以 '-' 开头）的 token 不可能是完整 SKU，所以这一粘是
    零歧义的，不用查字典。空格两侧都没有连字符的那种（`MCH-SS V2`）才需要
    字典裁决，见 learned.py。
    """
    out: list[dict] = []
    for c in row:
        prev = out[-1] if out else None
        # near_only（有表头时）：只粘挨着的。同一个框里拆出来的两段隔一个空格宽（约半个字高），
        # 隔着一列空白的两个框不粘 —— 装箱单里折行的描述 `… v1-` 和右边 SKU 列的 `MT-01`
        # 会被粘成 `v1-MT-01`。没有表头时不加这条：README 承诺那条路一切照旧，而
        # `SAN-NAT-` 和 `CRY` 被 OCR 读成隔得较开的两个框时，照旧要粘回去。
        near = prev is not None and (
            not near_only or c["x0"] - prev["x1"] <= max(prev["h"], c["h"]))
        if near and (prev["text"].endswith("-") or c["text"].startswith("-")):
            prev["text"] += c["text"]
            prev["x1"] = max(prev["x1"], c["x1"])
            prev["cx"] = (prev["x0"] + prev["x1"]) / 2
            prev["top"] = min(prev["top"], c["top"])
            prev["bot"] = max(prev["bot"], c["bot"])
            prev["cy"] = (prev["top"] + prev["bot"]) / 2
            prev["h"] = max(1.0, prev["bot"] - prev["top"])
            prev["score"] = min(prev["score"], c["score"])
            continue
        out.append(dict(c))
    return out


def _split_multi_sku(row: list[dict], pattern: re.Pattern, header: dict | None = None) -> list[list[dict]]:
    """一簇里出现多个 SKU（行距过小被聚到一起）时，按 y 就近拆开。

    有表头时只认 SKU 列里的锚点：描述里像 SKU 的词（`Sand-type`）也算锚点的话，
    一行会被拆成两行，数量还会被分给那个假行。"""
    anchors = [c for c in row
               if _in_sku_column(c, header) and _looks_like_sku(c["text"], pattern)]
    if len(anchors) <= 1:
        return [row]
    groups: list[list[dict]] = [[] for _ in anchors]
    for c in row:
        nearest = min(range(len(anchors)), key=lambda i: abs(anchors[i]["cy"] - c["cy"]))
        groups[nearest].append(c)
    return [sorted(g, key=lambda c: c["cx"]) for g in groups if g]


def parse_rows(items, sku_regex: str = DEFAULT_SKU_REGEX, min_score: float = DEFAULT_MIN_SCORE,
               lookup=None):
    """lookup 是可选的裁判（learned 模块），给出 fix/known/suggest/is_junk 四个方法。
    不传就是纯规则模式 —— 本模块因此仍然无状态、可单测。"""
    pattern = re.compile(sku_regex)

    cells: list[dict] = []
    for box, txt, score in items:
        cells.extend(_explode(box, txt, score))

    results = []
    header = _find_header(cells)
    if header is not None:  # 表头及以上（标题、两行表头的上一行 `USA -CAN`）不是数据行
        cells = [c for c in cells if c["cy"] > header["bottom"]]
    joined = [_join_dangling_dash(row, near_only=header is not None) for row in _cluster_rows(cells)]
    clusters = [sub for row in joined for sub in _split_multi_sku(row, pattern, header)]
    for row in clusters:
        sku_idx = None
        sku_val = None
        for i, cell in enumerate(row):
            if not _in_sku_column(cell, header):
                continue
            cand = _looks_like_sku(cell["text"], pattern)
            if cand:
                sku_idx, sku_val = i, cand
                break
        if sku_idx is None:
            continue

        # ADR-0001
        sku_end = sku_idx
        if lookup is not None:
            probe = row[sku_idx]["text"].strip()
            for j in range(sku_idx + 1, len(row)):
                probe = f"{probe} {row[j]['text'].strip()}"
                hit = lookup.known(probe)
                if not hit:
                    break
                sku_val, sku_end = hit, j

        # 分级套用：你亲口教过的直接改，大小写回正也直接改，模糊匹配只给建议。
        corrected_from = None
        suggestion = None
        if lookup is not None:
            hit = lookup.fix(sku_val) or lookup.known(sku_val)
            if hit and hit != sku_val:
                corrected_from, sku_val = sku_val, hit
            else:
                suggestion = lookup.suggest(sku_val)
        junk = bool(lookup is not None and lookup.is_junk(sku_val))

        # 数量：有表头就按列读（_qty_from_header）；否则 SKU 右侧最靠右的整数（也认 X3 / (3) 这类写法）
        qty = None
        qty_found = False
        if header is not None:
            qty = _qty_from_header(row[sku_end], cells, header)
            qty_found = qty is not None
        else:
            for cell in reversed(row[sku_end + 1:]):
                val = _read_qty(cell["text"].strip())
                if val is not None:
                    qty = val
                    qty_found = True
                    break
        if not qty_found or qty is None or qty <= 0:
            qty = 1

        # 描述：SKU 左侧、非纯数字的文本
        desc_parts = [
            c["text"] for c in row[:sku_idx] if not _INT_RE.match(c["text"].strip())
        ]
        description = " ".join(desc_parts).strip()

        score = float(row[sku_idx]["score"])
        results.append(
            {
                "sku": sku_val,
                "sku_raw": " ".join(c["text"].strip() for c in row[sku_idx:sku_end + 1]),
                "qty": qty,
                "qty_detected": qty_found,
                "description": description,
                "confidence": round(float(score), 3),
                "low_confidence": score < min_score,
                "row_y": row[sku_idx]["cy"],
                # 判过是误识别的照样列出来，只是默认不勾选 —— 静默丢行比多列一行糟得多
                "selected": not junk,
                "junk": junk,
                "corrected_from": corrected_from,
                "suggestion": suggestion,
            }
        )

    results.sort(key=lambda r: r["row_y"])
    return results


# --------------------------------------------------------------------------- #
# 纯文本粘贴解析
# --------------------------------------------------------------------------- #

# 数量写法：X3 / x3 / ×3 / *3 / 3X / (3) / 3
_QTY_MARKED_RE = re.compile(r"^[（(\[]?\s*(?:[Xx×✕*]\s*(\d{1,4})|(\d{1,4})\s*[Xx×✕*])\s*[）)\]]?$")
_QTY_PAREN_RE = re.compile(r"^[（(\[]\s*(\d{1,4})\s*[）)\]]$")


_ATTACHED_QTY_RE = re.compile(r"^(.+?)([Xx×✕*])(\d{1,4})$")


def _split_tokens(line: str, pattern: re.Pattern) -> list[str]:
    """先按制表符/逗号等分栏（兼容从 Excel 直接复制），再按空格拆词。"""
    tokens = []
    for field in re.split(r"[\t,;|]+", line):
        tokens.extend(field.split())
    tokens = [t for t in tokens if t]

    # 数量粘在 SKU 后面、中间没空格：PHC-ELE-BKX7 / PHC-ELE-BK×5 → 拆成两个 token。
    # 要求前缀含 '-' 且不以 '-' 结尾，这样 EU-PRO-X7 这种「X7 本身就是尾段」的不会被误拆。
    out = []
    for t in tokens:
        m = _ATTACHED_QTY_RE.match(t)
        if m and "-" in m.group(1) and not m.group(1).endswith("-") \
                and _looks_like_sku(m.group(1), pattern, correct_ocr=False):
            out.append(m.group(1))
            out.append("X" + m.group(3))
        else:
            out.append(t)
    return out


def _read_qty(token: str) -> int | None:
    m = _QTY_MARKED_RE.match(token)
    if m:
        return int(m.group(1) or m.group(2))
    m = _QTY_PAREN_RE.match(token)
    if m:
        return int(m.group(1))
    if _INT_RE.match(token):
        return int(token)
    return None


def _parse_text_raw(text: str) -> dict:
    """原文模式：整行就是要打印的内容，一个字符都不动。

    不拆词、不套正则、不做归一化 —— 逗号、空格、小写、中文都原样保留（`SC,ABC`
    在默认模式下会被逗号切成两栏，再被归一化吃掉逗号变成 `SCABC`）。
    只在行尾切掉带显式标记的数量（`X3` / `(3)`）；裸数字保留为正文的一部分，
    否则 `ABC 3` 这种本身带数字的内容会被误切。
    """
    rows = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        qty, qty_detected = 1, False
        parts = line.rsplit(None, 1)  # 按最后一段空白切开
        if len(parts) == 2:
            val = None
            if _QTY_MARKED_RE.match(parts[1]) or _QTY_PAREN_RE.match(parts[1]):
                val = _read_qty(parts[1])
            if val is not None and val > 0 and parts[0].strip():
                line, qty, qty_detected = parts[0].strip(), val, True
        rows.append(
            {
                "sku": line,
                "sku_raw": raw.strip(),
                "qty": qty,
                "qty_detected": qty_detected,
                "description": "",
                "confidence": 1.0,
                "low_confidence": False,
                "selected": True,
            }
        )
    return {"rows": rows, "unparsed": []}


def parse_text(text: str, sku_regex: str = DEFAULT_SKU_REGEX, raw: bool = False) -> dict:
    """逐行解析粘贴进来的清单，例如：

        PHC-MIN-MP-WLB X3
        PHC-GCL-DW-WLB X2
        PHC-ELE-BK X1

    也兼容从 Excel 复制的「描述<TAB>SKU<TAB>数量」以及 "3x SKU"、"SKU (2)" 等写法。

    raw=True 时整行原样当 SKU，见 _parse_text_raw()。
    """
    if raw:
        return _parse_text_raw(text)

    pattern = re.compile(sku_regex)
    rows: list[dict] = []
    unparsed: list[str] = []

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue

        tokens = _split_tokens(line, pattern)
        sku_idx = None
        sku_val = None
        for i, t in enumerate(tokens):
            cand = _looks_like_sku(t, pattern, correct_ocr=False)  # 粘贴文本不做形近纠正
            if cand:
                sku_idx, sku_val = i, cand
                break

        if sku_idx is None:
            unparsed.append(line)
            continue

        # 数量：先找带 X 标记的（不限左右），再退回 SKU 右侧最靠右的纯整数
        qty = None
        for t in tokens:
            m = _QTY_MARKED_RE.match(t) or _QTY_PAREN_RE.match(t)
            if m and t != tokens[sku_idx]:
                qty = _read_qty(t)
                break
        if qty is None:
            for t in reversed(tokens[sku_idx + 1:]):
                if _INT_RE.match(t):
                    qty = int(t)
                    break

        qty_detected = qty is not None and qty > 0
        description = " ".join(
            t for i, t in enumerate(tokens)
            if i < sku_idx and _read_qty(t) is None
        ).strip()

        rows.append(
            {
                "sku": sku_val,
                "sku_raw": tokens[sku_idx],
                "qty": qty if qty_detected else 1,
                "qty_detected": qty_detected,
                "description": description,
                "confidence": 1.0,
                "low_confidence": False,
                "selected": True,
            }
        )

    return {"rows": rows, "unparsed": unparsed}


def recognize(
    image_bytes: bytes,
    sku_regex: str = DEFAULT_SKU_REGEX,
    min_score: float = DEFAULT_MIN_SCORE,
    lookup=None,
) -> dict:
    engine = get_engine()
    result = engine(image_bytes)
    items = _raw_items(result)
    rows = parse_rows(items, sku_regex, min_score, lookup=lookup)
    return {
        "rows": rows,
        "raw_count": len(items),
        "raw_texts": [t for _, t, _ in items],
    }
