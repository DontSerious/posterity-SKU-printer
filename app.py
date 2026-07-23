"""SKU 条码识别打印工具 —— 本地 Web 服务。

流程：上传/粘贴截图 → 自动 OCR → 表格核对 → 点「确定打印」才出纸。
/api/ocr 只识别不打印；只有 /api/print 会真正驱动打印机。
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import threading
import webbrowser

from flask import Flask, jsonify, request, send_file, render_template
import io

import label as label_mod
import ocr as ocr_mod

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 截图够用了

DEFAULT_CONFIG = {
    "printer": "",
    "paper": {"mode": "custom", "paper_id": 256, "width_mm": 60.0, "height_mm": 30.0},
    "layout": label_mod.Layout().to_dict(),
    "ocr": {"sku_regex": ocr_mod.DEFAULT_SKU_REGEX, "min_score": ocr_mod.DEFAULT_MIN_SCORE},
}


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            saved = json.load(f)
        for k, v in saved.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _paper_from(payload: dict):
    from printing import PaperSpec

    p = payload.get("paper") or {}
    return PaperSpec(
        width_mm=float(p.get("width_mm", 60.0)),
        height_mm=float(p.get("height_mm", 30.0)),
        paper_id=int(p.get("paper_id", 256)),
    )


def _image_bytes_from_request() -> bytes | None:
    f = request.files.get("image")
    if f:
        return f.read()
    data = request.get_json(silent=True) or {}
    b64 = data.get("image_base64") or ""
    if b64:
        if "," in b64[:64]:
            b64 = b64.split(",", 1)[1]
        return base64.b64decode(b64)
    return None


# --------------------------------------------------------------------------- #
# 页面
# --------------------------------------------------------------------------- #

@app.get("/")
def index():
    return render_template("index.html")


# --------------------------------------------------------------------------- #
# 只读接口
# --------------------------------------------------------------------------- #

@app.get("/api/printers")
def api_printers():
    import printing

    printers = printing.list_printers()
    cfg = load_config()
    selected = cfg.get("printer") or printing.get_default_printer()
    if selected and not any(p["name"] == selected for p in printers):
        selected = printers[0]["name"] if printers else ""
    return jsonify(
        {
            "printers": printers,
            "selected": selected,
            "papers": printing.list_papers(selected) if selected else [],
            "resolution": printing.printer_resolution(selected) if selected else {},
        }
    )


@app.get("/api/papers")
def api_papers():
    import printing

    printer = request.args.get("printer", "")
    return jsonify(
        {
            "papers": printing.list_papers(printer),
            "resolution": printing.printer_resolution(printer),
        }
    )


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        return jsonify(load_config())
    cfg = load_config()
    payload = request.get_json(silent=True) or {}
    for k, v in payload.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    save_config(cfg)
    return jsonify(cfg)


# --------------------------------------------------------------------------- #
# 识别（不打印）
# --------------------------------------------------------------------------- #

@app.post("/api/ocr")
def api_ocr():
    img = _image_bytes_from_request()
    if not img:
        return jsonify({"ok": False, "error": "没有收到图片"}), 400

    cfg = load_config()
    ocr_cfg = cfg.get("ocr", {})
    try:
        result = ocr_mod.recognize(
            img,
            sku_regex=ocr_cfg.get("sku_regex", ocr_mod.DEFAULT_SKU_REGEX),
            min_score=float(ocr_cfg.get("min_score", ocr_mod.DEFAULT_MIN_SCORE)),
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"OCR 失败: {exc}"}), 500

    return jsonify({"ok": True, **result})


@app.post("/api/parse-text")
def api_parse_text():
    """解析粘贴进来的文本清单，纯字符串处理，不走 OCR。"""
    payload = request.get_json(silent=True) or {}
    text = payload.get("text") or ""
    if not text.strip():
        return jsonify({"ok": False, "error": "没有收到文本"}), 400

    ocr_cfg = load_config().get("ocr", {})
    try:
        result = ocr_mod.parse_text(text, sku_regex=ocr_cfg.get("sku_regex", ocr_mod.DEFAULT_SKU_REGEX))
    except re.error as exc:
        return jsonify({"ok": False, "error": f"config.json 里的 sku_regex 有问题: {exc}"}), 400
    return jsonify({"ok": True, **result})


@app.post("/api/preview")
def api_preview():
    payload = request.get_json(silent=True) or {}
    sku = (payload.get("sku") or "").strip()
    if not sku:
        return jsonify({"ok": False, "error": "缺少 SKU"}), 400
    paper = _paper_from(payload)
    layout = payload.get("layout") or {}
    try:
        png = label_mod.render_png(sku, paper.width_mm, paper.height_mm, layout)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400
    return send_file(io.BytesIO(png), mimetype="image/png")


@app.post("/api/barcode-info")
def api_barcode_info():
    payload = request.get_json(silent=True) or {}
    sku = (payload.get("sku") or "").strip()
    if not sku:
        return jsonify({"ok": False, "error": "缺少 SKU"}), 400
    paper = _paper_from(payload)
    try:
        return jsonify({"ok": True, **label_mod.barcode_info(sku, paper.width_mm, payload.get("layout"))})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400


# --------------------------------------------------------------------------- #
# 打印（唯一会出纸的接口）
# --------------------------------------------------------------------------- #

@app.post("/api/print")
def api_print():
    import printing

    payload = request.get_json(silent=True) or {}
    rows = [r for r in (payload.get("rows") or []) if r.get("sku")]
    if not rows:
        return jsonify({"ok": False, "error": "没有勾选任何要打印的行"}), 400

    paper = _paper_from(payload)
    layout = payload.get("layout") or {}
    dry_run = bool(payload.get("dry_run"))

    # 打印前把当前选择记下来，下次打开就是这套参数
    cfg = load_config()
    cfg["printer"] = payload.get("printer") or cfg.get("printer", "")
    cfg["paper"] = {
        "mode": payload.get("paper", {}).get("mode", "custom"),
        "paper_id": paper.paper_id,
        "width_mm": paper.width_mm,
        "height_mm": paper.height_mm,
    }
    if layout:
        cfg["layout"].update(layout)
    save_config(cfg)

    try:
        if dry_run:
            res = printing.dry_run_labels(rows, paper, layout, os.path.join(BASE_DIR, "output"))
        else:
            printer = payload.get("printer") or ""
            if not printer:
                return jsonify({"ok": False, "error": "没有选择打印机"}), 400
            res = printing.print_labels(printer, rows, paper, layout)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"打印失败: {exc}"}), 500

    return jsonify(res)


PORT = 5000
URL = f"http://127.0.0.1:{PORT}"

# 直接按绝对路径拉浏览器。有些机器 http 协议的 UserChoice 指向的 ProgId 没有
# shell\open\command 注册项，这时候 start / os.startfile 都会弹「选择打开方式」。
_BROWSER_CANDIDATES = [
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles%\Mozilla Firefox\firefox.exe",
]


def _find_browser() -> str | None:
    for raw in _BROWSER_CANDIDATES:
        path = os.path.expandvars(raw)
        if "%" not in path and os.path.isfile(path):
            return path
    return None


def _open_browser():
    exe = _find_browser()
    if exe:
        try:
            subprocess.Popen([exe, URL], close_fds=True)
            return
        except OSError:
            pass
    try:
        webbrowser.open(URL)  # 系统 http 关联正常时走这条
    except Exception:  # noqa: BLE001
        pass


def _arg_value(flag: str) -> str | None:
    """取 `--flag value` 或 `--flag=value` 的值。"""
    for i, a in enumerate(sys.argv):
        if a == flag and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return None


def _lan_ips() -> list[str]:
    """本机的局域网 IPv4，用来提示别的主机该访问哪个地址。"""
    import socket

    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:  # 兜底：问一下路由表挑哪个网卡出网
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith(("127.", "169.254.")))


if __name__ == "__main__":
    # cmd 的默认代码页不是 UTF-8，控制台里打中文会抛 UnicodeEncodeError 直接崩
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    # 默认只监听本机。加 --lan 才对局域网开放 —— 开放后同一网段的任何人都能触发打印。
    lan = "--lan" in sys.argv or os.environ.get("SKU_LAN") == "1"
    host = "0.0.0.0" if lan else "127.0.0.1"

    # 开机自启/后台常驻时不要弹浏览器
    no_browser = "--no-browser" in sys.argv or os.environ.get("SKU_NO_BROWSER") == "1"

    # 后台无窗口跑的时候把输出落盘，否则崩了什么都看不到
    log_path = _arg_value("--log") or os.environ.get("SKU_LOG")
    if log_path:
        os.makedirs(os.path.dirname(os.path.abspath(log_path)) or ".", exist_ok=True)
        log_file = open(log_path, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log_file
        print(f"\n===== 启动 {__import__('datetime').datetime.now():%Y-%m-%d %H:%M:%S} =====")

    # 改代码自动重启。模板和静态文件不用重启 —— 关掉缓存后刷新浏览器即可生效。
    reload_on = "--reload" in sys.argv or os.environ.get("SKU_RELOAD") == "1"
    extra_files = None
    if reload_on:
        app.config["TEMPLATES_AUTO_RELOAD"] = True
        app.jinja_env.auto_reload = True
        app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
        # 显式列出本项目的 .py：printing/label/ocr 是延迟导入的，
        # 首次请求之前不在 sys.modules 里，reloader 看不到它们
        extra_files = [
            os.path.join(BASE_DIR, f)
            for f in ("app.py", "ocr.py", "label.py", "printing.py", "config.json")
        ]

    if not no_browser and not os.environ.get("WERKZEUG_RUN_MAIN"):
        threading.Timer(1.2, _open_browser).start()

    print(f"\n  SKU 条码打印  ->  {URL}   （浏览器没自动弹出就手动复制这个地址）")
    if lan:
        print("\n  局域网已开放，其他主机可访问：")
        for ip in _lan_ips():
            print(f"      http://{ip}:{PORT}")
        print("\n  连不上的话，多半是防火墙拦了入站。用管理员 PowerShell 放行一次：")
        print(f'      New-NetFirewallRule -DisplayName "SKU Barcode Printer" '
              f'-Direction Inbound -Protocol TCP -LocalPort {PORT} -Action Allow')
        print("\n  注意：同一网段的任何人打开这个地址都能直接驱动打印机出纸。")
    if reload_on:
        print("\n  自动重载已开启：改 .py 会自动重启；改模板/前端刷新浏览器即可。")
    print()
    # debug 一直保持 False：Werkzeug 的调试器能在页面上执行任意代码，
    # 对局域网开放时绝不能开。只借用它的 reloader。
    app.run(host=host, port=PORT, debug=False, use_reloader=reload_on, extra_files=extra_files)
