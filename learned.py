"""从人工修正里学到的东西 —— 白名单字典、错→对映射、误识别黑名单。

OCR 侧的确定性规则（ocr.py）只能纠有把握的错。剩下的那些「机器分不出、
人一眼就知道」的，靠这里积累：

    白名单 skus   打印成功 +1；人工亲手敲的直接算数（人不会把 O 敲成 0）。
                  攒够 THRESHOLD 次才当模糊匹配的目标 —— 一次误打印不至于
                  立刻把整本字典带偏。
    映射 fixes    你把 X 改成 Y 时记下来，下次见 X 直接改。人明确教过的，
                  不设门槛。
    黑名单 junk   你判过「这不是 SKU」的串，下次照样列出来但默认不勾选。
                  **不静默丢行** —— 打标签这行当口，少打一张且无人察觉，
                  比多列一行糟得多。

存成一个人可读的 JSON，随时能打开删几行。写入走 tmp + os.replace，
中途断电不会留下半个文件。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_PATH = os.path.join(BASE_DIR, "learned.json")

# 打印成功这类弱信号要攒够几次才敢当模糊匹配的目标。人工敲的不受此限。
THRESHOLD = 2

# 形近等价类。同一类里的字符互相可换 —— 模糊匹配只认这种差异，
# 别的一律不认（WLA 和 WLB 是两个真实存在的型号，绝不能互相靠）。
_CONFUSE_GROUPS = ("O0QD", "I1L", "S5", "B8", "Z2", "G6", "T7")
_CLASS = {ch: i for i, g in enumerate(_CONFUSE_GROUPS) for ch in g}

_lock = threading.RLock()
_cache: dict | None = None


# --------------------------------------------------------------------------- #
# 存取
# --------------------------------------------------------------------------- #

def _empty() -> dict:
    return {"version": 1, "skus": {}, "fixes": {}, "junk": {}}


def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    with _lock:
        if _cache is None:
            data = _empty()
            try:
                with open(STORE_PATH, encoding="utf-8") as f:
                    saved = json.load(f)
                for k in ("skus", "fixes", "junk"):
                    if isinstance(saved.get(k), dict):
                        data[k] = saved[k]
            except (OSError, ValueError):
                pass  # 文件不存在或写坏了：当空的重新开始，不要让打印页起不来
            _cache = data
    return _cache


def _save() -> None:
    """原子写。绝不能把 learned.json 加进 app.py 的 reloader extra_files ——
    理由同 config.json：打印途中被重启，spooler 只收到 0 字节的残废作业。"""
    with _lock:
        tmp = STORE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_load(), f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, STORE_PATH)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def reload_from_disk() -> None:
    """测试用：丢掉内存缓存，下次访问重新读文件。"""
    global _cache
    with _lock:
        _cache = None


# --------------------------------------------------------------------------- #
# 写入 —— 人工修正的落点
# --------------------------------------------------------------------------- #

def record_sku(sku: str, *, strong: bool = False) -> None:
    """确认一个 SKU 是对的。

    strong=True 用于人工亲手敲进去的值：直接算够数，第一次就能帮到你
    （冷启动时字典是空的，等「攒两次」会让这个功能好几周都不存在）。
    """
    sku = (sku or "").strip()
    if not sku:
        return
    with _lock:
        data = _load()
        entry = data["skus"].get(sku) or {"n": 0, "last": ""}
        entry["n"] = THRESHOLD if strong else int(entry.get("n") or 0) + 1
        entry["last"] = _now()
        data["skus"][sku] = entry
        data["junk"].pop(sku, None)  # 又被确认为真 SKU，就不该还在黑名单里
        _save()


def record_fix(wrong: str, right: str) -> None:
    """记下一次人工改正：以后见 wrong 直接出 right。

    顺带自愈：wrong 如果在白名单里，说明它当初是误进去的（你刚亲口判它是
    错的），就地删掉 —— 否则模糊匹配会一直拿它当靠拢目标，越用越歪。
    """
    wrong = (wrong or "").strip()
    right = (right or "").strip()
    if not right or wrong == right:
        return
    with _lock:
        data = _load()
        if wrong:
            data["fixes"][wrong] = right
            data["skus"].pop(wrong, None)
        _save()
    record_sku(right, strong=True)


def record_junk(text: str) -> None:
    """记下「这串不是 SKU」。"""
    text = (text or "").strip()
    if not text:
        return
    with _lock:
        data = _load()
        entry = data["junk"].get(text) or {"n": 0, "last": ""}
        entry["n"] = int(entry.get("n") or 0) + 1
        entry["last"] = _now()
        data["junk"][text] = entry
        data["skus"].pop(text, None)
        _save()


def forget(kind: str, key: str) -> bool:
    """管理面板用：删掉一条。kind ∈ skus / fixes / junk。"""
    if kind not in ("skus", "fixes", "junk"):
        return False
    with _lock:
        data = _load()
        if key not in data[kind]:
            return False
        data[kind].pop(key)
        _save()
    return True


def snapshot() -> dict:
    """管理面板用：三张表的当前内容。"""
    data = _load()
    return {
        "threshold": THRESHOLD,
        "skus": [{"sku": k, "n": v.get("n", 0), "last": v.get("last", ""),
                  "active": int(v.get("n") or 0) >= THRESHOLD}
                 for k, v in sorted(data["skus"].items())],
        "fixes": [{"wrong": k, "right": v} for k, v in sorted(data["fixes"].items())],
        "junk": [{"text": k, "n": v.get("n", 0), "last": v.get("last", "")}
                 for k, v in sorted(data["junk"].items())],
    }


# --------------------------------------------------------------------------- #
# 读取 —— OCR 侧的裁决接口（ocr.parse_rows 通过 lookup= 注入）
# --------------------------------------------------------------------------- #

def _pool() -> dict[str, str]:
    """够格当匹配目标的 SKU：小写 → 原样写法。"""
    return {k.lower(): k for k, v in _load()["skus"].items()
            if int(v.get("n") or 0) >= THRESHOLD}


def fix(text: str) -> str | None:
    """精确映射命中 —— 你亲口教过这一串，可以直接改。"""
    return _load()["fixes"].get((text or "").strip())


def known(text: str) -> str | None:
    """忽略大小写的字典命中，返回字典里的规范写法。

    干两件事：把 OCR 读岔的大小写回正（UK4-SS-MN-… → …-mn-…），以及给
    合并裁决当裁判（MCH-SS + V2 拼出来的串在不在字典里）。
    """
    return _pool().get((text or "").strip().lower())


def _confusable(a: str, b: str) -> bool:
    """两串是否只差若干个形近字。长度必须相同 —— 段结构因此自动一致。"""
    if len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b):
        if x == y:
            continue
        ux, uy = x.upper(), y.upper()
        if ux == uy:
            continue  # 纯大小写差异归 known() 管，不算形近差异
        cx = _CLASS.get(ux)
        if cx is not None and cx == _CLASS.get(uy):
            diff += 1
            continue
        return False
    return diff > 0


def suggest(text: str) -> str | None:
    """模糊匹配 —— 只在候选唯一时给一条建议，绝不自动改。

    自动改的风险是把一个真的新 SKU 静默改成旧的，那种错没人会发现。
    """
    text = (text or "").strip()
    if not text:
        return None
    pool = _pool()
    if text.lower() in pool:
        return None
    hits = {v for v in pool.values() if _confusable(text, v)}
    return hits.pop() if len(hits) == 1 else None


def is_junk(text: str) -> bool:
    return (text or "").strip() in _load()["junk"]
