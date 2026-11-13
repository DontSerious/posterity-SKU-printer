"""SKU 库 —— 「SKU 库」页签的数据源：一份人维护的 SKU 清单，搜出来选中再打。

存在 SQLite（skus.db，同目录，不进 git）。只有一张表：

    skus(sku PRIMARY KEY, description, created, updated)

SKU 区分大小写（`UK4-SS-mn-GL-AU-BK` 里的小写是真的），搜索在前端做、不分大小写。

条目三个来路：
- 页面上手动加 / 改 / 删，或者批量粘贴（每行 `SKU`，可选 `⇥描述`）；
- /api/print 真打印成功后，库里没有的 SKU 自动收进来（干跑不算）；已有的不动，
  免得把你手改过的描述冲掉；
- **库文件第一次创建时从 learned.json 的 SKU 字典导入**（那是打印成功 / 人工确认
  过的 SKU）。只导这一次，删了 skus.db 才会再导。

和 learned.json 是两回事：那份是 OCR 纠错的字典，会被人工修正改写、删条目；这份是
你认可的清单。删库里的条目不影响纠错，反之亦然。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "skus.db")
MAX_LEN = 200

_init_lock = threading.Lock()
_ready_for: str | None = None  # 已经建过表的库路径（测试会换 DB_PATH）


class Conflict(ValueError):
    """SKU 已经在库里（新增或改名撞上了）。"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    global _ready_for
    path = DB_PATH
    with _init_lock:
        fresh = not os.path.exists(path)
        conn = sqlite3.connect(path, timeout=10)
        if _ready_for != path:
            conn.execute("""CREATE TABLE IF NOT EXISTS skus (
                sku TEXT PRIMARY KEY,
                description TEXT NOT NULL DEFAULT '',
                created TEXT NOT NULL,
                updated TEXT NOT NULL)""")
            if fresh:
                _seed(conn)
            conn.commit()
            _ready_for = path
    return conn


@contextmanager
def _db():
    """一次操作一条连接：成功提交、出错回滚，最后一定关掉。
    （sqlite3 连接自己的 `with` 只管提交 / 回滚，不关连接。）"""
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _seed(conn: sqlite3.Connection) -> None:
    import learned

    now = _now()
    conn.executemany(
        "INSERT OR IGNORE INTO skus (sku, description, created, updated) VALUES (?, '', ?, ?)",
        [(s["sku"], s.get("last") or now, now) for s in learned.snapshot()["skus"]],
    )


def _clean(sku: str) -> str:
    sku = (sku or "").strip()
    if not sku:
        raise ValueError("SKU 不能为空")
    if len(sku) > MAX_LEN:
        raise ValueError(f"SKU 超过 {MAX_LEN} 个字符")
    return sku


def items() -> list[dict]:
    with _db() as conn:
        rows = conn.execute("SELECT sku, description, created, updated FROM skus ORDER BY sku").fetchall()
    return [{"sku": s, "description": d, "created": c, "updated": u} for s, d, c, u in rows]


def add(sku: str, description: str = "") -> None:
    sku = _clean(sku)
    now = _now()
    try:
        with _db() as conn:
            conn.execute("INSERT INTO skus (sku, description, created, updated) VALUES (?, ?, ?, ?)",
                         (sku, (description or "").strip(), now, now))
    except sqlite3.IntegrityError:
        raise Conflict(f"{sku} 已经在库里") from None


def update(old_sku: str, sku: str, description: str = "") -> None:
    """改描述，也可以改名（改名撞上已有的 SKU 报 Conflict）。"""
    old_sku = (old_sku or "").strip()
    sku = _clean(sku)
    try:
        with _db() as conn:
            cur = conn.execute("UPDATE skus SET sku = ?, description = ?, updated = ? WHERE sku = ?",
                               (sku, (description or "").strip(), _now(), old_sku))
            if cur.rowcount == 0:
                raise KeyError(f"库里没有 {old_sku}")
    except sqlite3.IntegrityError:
        raise Conflict(f"{sku} 已经在库里") from None


def delete(sku: str) -> bool:
    with _db() as conn:
        return conn.execute("DELETE FROM skus WHERE sku = ?", ((sku or "").strip(),)).rowcount > 0


def import_text(text: str) -> dict:
    """批量粘贴：每行一个 SKU，后面可以跟一个 Tab 再写描述（从 Excel 复制两列就是这样）。

    只认 Tab 当分隔：SKU 里可能有空格（`MCH-SS V2`）和逗号（按原文打的 `SC,ABC`）。
    已在库里的跳过，不改它的描述。"""
    added, skipped, bad = [], [], []
    now = _now()
    with _db() as conn:
        for line in (text or "").splitlines():
            if not line.strip():
                continue
            sku, _, desc = line.partition("\t")
            try:
                sku = _clean(sku)
            except ValueError:
                bad.append(line.strip())
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO skus (sku, description, created, updated) VALUES (?, ?, ?, ?)",
                (sku, desc.strip(), now, now))
            (added if cur.rowcount else skipped).append(sku)
    return {"added": added, "skipped": skipped, "bad": bad}


def record_printed(rows: list[dict]) -> list[str]:
    """/api/print 真打印成功后调：库里没有的 SKU 收进来，返回新收的那些。"""
    now = _now()
    added = []
    with _db() as conn:
        for r in rows:
            sku = (r.get("sku") or "").strip()
            if not sku or len(sku) > MAX_LEN:
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO skus (sku, description, created, updated) VALUES (?, ?, ?, ?)",
                (sku, (r.get("description") or "").strip(), now, now))
            if cur.rowcount:
                added.append(sku)
    return added
