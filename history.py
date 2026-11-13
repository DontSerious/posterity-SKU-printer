"""打印记录 —— 「历史」页签的数据源：打过哪些 SKU，搜出来再打一次。

为什么不直接读 learned.json 的 SKU 字典：那是给 OCR 纠错用的，语义不同 ——
人工敲进去的会把次数直接改写成 THRESHOLD，你把 X 改成 Y、或判 X 为误识别时
X 会被删掉，也没有数量和描述。拿它当打印记录，搜出来的东西既不全也不准。

    skus[sku] = {"times": 真打印成功的次数, "last_qty": 上次打了几张,
                 "last": 上次时间, "desc": 上次的描述}

只有 /api/print 真出纸成功才记（干跑不算，打印文件 / ERP 打印桥不是 SKU 标签，不记）。

**第一次启动时从 learned.json 导入**（history.json 还不存在时）：导进来的只有
SKU 和时间，times / last_qty 记 0，页面上显示成「旧记录」。导完立刻落盘，
以后不再导 —— 删了 history.json 才会重导一次。

存储方式照抄 learned.py：人可读的 JSON，tmp + os.replace 原子写。
和 config.json / learned.json 一样**绝不能**进 app.py 的 reloader extra_files。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_PATH = os.path.join(BASE_DIR, "history.json")

_lock = threading.RLock()
_cache: dict | None = None


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _seed() -> dict:
    """从 learned.json 的 SKU 字典导入。只有 SKU 和时间是可信的。"""
    import learned

    skus = {}
    for s in learned.snapshot()["skus"]:
        skus[s["sku"]] = {"times": 0, "last_qty": 0, "last": s.get("last") or "", "desc": ""}
    return {"version": 1, "seeded_from_learned": len(skus), "skus": skus}


def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    with _lock:
        if _cache is None:
            try:
                with open(STORE_PATH, encoding="utf-8") as f:
                    saved = json.load(f)
                if not isinstance(saved.get("skus"), dict):
                    raise ValueError("skus 不是 dict")
                _cache = saved
            except FileNotFoundError:
                _cache = _seed()
                _save()
            except (OSError, ValueError):
                # 写坏了：当空的，但不覆盖原文件（下次成功打印才会写），留着能人工救
                _cache = {"version": 1, "skus": {}}
    return _cache


def _save() -> None:
    with _lock:
        tmp = STORE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_load(), f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, STORE_PATH)


def reload_from_disk() -> None:
    """测试用：丢掉内存缓存，下次访问重新读文件。"""
    global _cache
    with _lock:
        _cache = None


def record(rows: list[dict]) -> None:
    """一次真打印成功后调用。rows 同 /api/print：[{sku, qty, description?}]。"""
    with _lock:
        data = _load()
        now = _now()
        for r in rows:
            sku = (r.get("sku") or "").strip()
            if not sku:
                continue
            e = data["skus"].get(sku) or {"times": 0, "last_qty": 0, "last": "", "desc": ""}
            e["times"] = int(e.get("times") or 0) + 1
            e["last_qty"] = max(1, int(r.get("qty") or 1))
            e["last"] = now
            desc = (r.get("description") or "").strip()
            if desc:
                e["desc"] = desc  # 这次没写描述就留着上次的
            data["skus"][sku] = e
        _save()


def forget(sku: str) -> bool:
    with _lock:
        data = _load()
        if sku not in data["skus"]:
            return False
        data["skus"].pop(sku)
        _save()
    return True


def items() -> list[dict]:
    """全部记录，上次打印时间倒序。搜索在前端做（几百条，一次拉完）。"""
    data = _load()
    out = [{"sku": k, "times": int(v.get("times") or 0), "last_qty": int(v.get("last_qty") or 0),
            "last": v.get("last") or "", "desc": v.get("desc") or ""}
           for k, v in data["skus"].items()]
    out.sort(key=lambda x: (x["last"], x["sku"]), reverse=True)
    return out
