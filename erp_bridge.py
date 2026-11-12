"""ERP 打印桥 —— 出站轮询 ERP 的打印队列，在本机打印。

ERP 在公网 VPS 上，永远进不来办公室局域网；浏览器在 HTTPS 页面里也不能
fetch http://192.168.x.x（混合内容）。所以方向反过来：这边每隔几秒 POST
一次 ERP 的 /api/print-bridge/poll（带 X-Print-Token），顺便上报本机的
打印机列表（ERP 的标签页用它渲染下拉框）；拿到任务就把 PDF 栅格化后
打印，再 POST /api/print-bridge/result 回报。**不开任何入站端口。**

配置在 config.json 的 "erp" 块：
    "erp": {
      "enabled": true,
      "url": "https://erp.posterityindustries.com",
      "token": "<ERP 设置里的 print_bridge_token>",
      "name": "office",
      "poll_seconds": 3
    }
config.json 每轮重读，改了 token/开关不用重启。
"""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.error
import urllib.request

_TIMEOUT = 25

# 桥的健康状态，给首页那个面板读（app.py 的 /api/panel/status）。
# 在这里而不是在日志里，是因为"桥现在通不通"以前只能靠翻 logs/service.log 里的
# [erp-bridge] 行才知道 —— 而它断了不会有任何人被通知。只写不读，线程安全靠
# GIL：每个字段都是一次整体赋值。
status = {"last_ok": 0.0, "last_error": "", "last_error_at": 0.0, "jobs": 0}


def _post(url: str, token: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Print-Token": token},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return json.loads(r.read().decode())


def _handle_job(job: dict, cfg_erp: dict) -> dict:
    """栅格化 PDF 并打印。返回要回报给 ERP 的 result 载荷。"""
    import printing
    from app import _pages_from_upload   # app 已完全加载，无循环导入问题

    data = base64.b64decode(job["pdf_base64"])
    pages, w_mm, h_mm = _pages_from_upload(data, job.get("filename") or "job.pdf", dpi=300)
    if w_mm:
        paper = printing.PaperSpec(width_mm=w_mm, height_mm=h_mm)
    else:  # 理论上 ERP 只发 PDF；兜底用默认纸张
        paper = printing.PaperSpec()
    printer = job.get("printer") or cfg_erp.get("printer") or printing.get_default_printer()
    if not printer:
        return {"job_id": job["id"], "ok": False, "error": "本机没有可用打印机"}
    res = printing.print_images(printer, pages, paper, int(job.get("copies") or 1),
                                job_name=job.get("filename") or "ERP labels")
    if not res.get("ok"):
        return {"job_id": job["id"], "ok": False, "error": res.get("error") or "打印失败"}
    return {"job_id": job["id"], "ok": True, "printed": res.get("printed", 0)}


def _loop(cfg_loader):
    import printing

    erp: dict = {}
    while True:
        try:
            erp = (cfg_loader() or {}).get("erp") or {}
            if not erp.get("enabled") or not erp.get("url") or not erp.get("token"):
                time.sleep(10)
                continue
            base = str(erp["url"]).rstrip("/")
            token = str(erp["token"])
            try:
                # 只报标签机：gw 上的 A4 打印机（kind=office）只给面板网页用，
                # ERP 那边看不到它（用户 2026-11-11 定）。Windows 上的条目没有 kind。
                printers = [{"name": p["name"], "is_default": p["is_default"]}
                            for p in printing.list_printers()
                            if p.get("kind", "label") == "label"]
            except Exception:  # noqa: BLE001
                printers = []
            r = _post(f"{base}/api/print-bridge/poll", token,
                      {"name": erp.get("name") or "office", "printers": printers})
            status["last_ok"] = time.time()
            status["last_error"] = ""
            job = (r or {}).get("job")
            if job:
                status["jobs"] += 1
                try:
                    result = _handle_job(job, erp)
                except Exception as exc:  # noqa: BLE001
                    result = {"job_id": job["id"], "ok": False, "error": str(exc)}
                _post(f"{base}/api/print-bridge/result", token, result)
                state = "OK" if result.get("ok") else f"FAIL: {result.get('error')}"
                print(f"[erp-bridge] job {job['id']} -> {state}")
                continue          # 队列可能还有任务，马上再问一轮
        except urllib.error.HTTPError as exc:
            status["last_error"] = f"HTTP {exc.code} {exc.reason}"
            status["last_error_at"] = time.time()
            if exc.code == 401:
                status["last_error"] = "token 被 ERP 拒绝（401）"
                print("[erp-bridge] token 被 ERP 拒绝（401）—— 核对 config.json 的 erp.token")
                time.sleep(30)
                continue
            print(f"[erp-bridge] HTTP {exc.code}: {exc.reason}")
        except Exception as exc:  # noqa: BLE001
            status["last_error"] = f"{type(exc).__name__}: {exc}"
            status["last_error_at"] = time.time()
            print(f"[erp-bridge] {type(exc).__name__}: {exc}")
        try:
            delay = max(2, int(erp.get("poll_seconds") or 3))
        except (TypeError, ValueError):
            delay = 3
        time.sleep(delay)


def start(cfg_loader) -> None:
    """从 app.py 调用一次；守护线程，主进程退出即结束。"""
    threading.Thread(target=_loop, args=(cfg_loader,), daemon=True,
                     name="erp-print-bridge").start()
    print("[erp-bridge] 已启动（config.json → erp.enabled 控制开关）")
