# SKU 条码识别打印工具

订单表格截图 → OCR 识别 SKU 和数量 → 人工核对 → 打印 Code128 标签到 **CTPrinter CT-428D**。

> **2026-11-11 起打印机接在 gw（`tomatoServer`，Debian）上，生产服务跑在那台**：
> <http://192.168.5.9:5000>，systemd 服务 `sku-printer`。怎么部署、怎么重启见下面
> 「在 gw（Linux）上跑」。office 那台 Windows 上的计划任务已停用，下面 Windows 的
> 用法照旧可用，只是那台已经没有打印机了。

## 使用

双击 **`run.bat`**（首次会自动 `uv sync` 装依赖），浏览器打开 <http://127.0.0.1:5000>。

1. 两种输入方式，页签切换：
   - **上传截图** —— 把 Excel 表格截图 **拖进去 / 点击选择 / 直接 `Ctrl+V` 粘贴**，自动 OCR
   - **粘贴文本** —— 直接贴清单，`Ctrl+Enter` 或点「解析文本」
     ```
     PHC-MIN-MP-WLB X3        MT-161     1
     PHC-GCL-DW-WLB X2        MT-162    2
     PHC-ELE-BK X1            MT-163    1
     ```
     数量支持 `X3` / `x3` / `×3` / `3X` / `(3)` / 纯数字 / 紧贴写法 `PHC-ELE-BKX7`；
     也能直接贴从 Excel 复制的「描述 ⇥ SKU ⇥ 数量」整行。不写数量按 1 张算并标黄。
     认不出 SKU 的行不会静默丢掉，会列出来告诉你跳过了哪些。
     勾 **「按原文」** 则整行原样打印：不拆词、不套正则、不做字符归一化，逗号/空格/小写/中文都保留
     （`SC,ABC` 默认会被逗号当成分栏切开、再被归一化吃掉逗号变成 `SCABC`，勾上就是 `SC,ABC`）。
     此模式只切行尾带显式标记的数量 `X3` / `(3)`，裸数字算正文的一部分。
2. 出一张可编辑的表格 —— 标黄的行表示置信度低或数量没读到，需要人工确认
3. 右侧选打印机、标签纸尺寸、微调版式，预览实时刷新
4. 点 **「确定打印」**，二次确认后才出纸

> 只有「确定打印」会驱动打印机。想先看效果就点 **「干跑」**，标签会存成 PNG 到 `output/`，不费标签纸。

## SKU 库（从库里挑来打）

第四个页签 **「SKU 库」**：一份自己维护的 SKU 清单（SKU + 可选描述），按 SKU 排序。

- 搜索框按 SKU 或描述做包含匹配，不分大小写；只剩一条时按回车就勾上。勾选跨搜索保留，
  可以搜几次、勾几条，最后一起点 **「加入核对列表」**。
- 加进去的行数量默认 **1 张**，在核对列表里改，打印照常走「确定打印」和二次确认。
  已经在列表里的 SKU 不会重复加，只是重新勾上，数量不动。
- 维护：每条右边 ✎ 改（可改名）、× 删；搜不到时提示里有「加入库」一键把搜索词加进去；
  「添加 / 批量导入」里每行一个 SKU，后面可以跟一个 **Tab** 再写描述（从 Excel 复制两列就是这样）。
  只认 Tab 当分隔，因为 SKU 里可能有空格和逗号。已在库里的跳过、不改描述。
- **自动入库**：真打印成功后，库里没有的 SKU 自动收进来（描述取核对列表里那一格）。干跑不算，
  「打印文件」和 ERP 打印桥不是 SKU 标签，也不算。已在库里的不动。
- 存在 `skus.db`（SQLite，`skudb.py`），和 `learned.json` 的纠错字典是两本账，删改互不影响。
  `skus.db` 第一次创建时把 `learned.json` 里已有的 SKU 导进来起步；删了 `skus.db` 才会再导一次。
  要备份就拷这个文件；要直接看：`sqlite3 skus.db "select * from skus"`。

## 打印文件（ERP 二维码标签 PDF）

第三个页签 **「打印文件」**：把 PDF（或 PNG）拖进去 → 显示页数、页面尺寸和第一页预览 →
选份数 → 下方「打印」里的「确定打印」。

- **和 SKU 标签共用下方「打印设置」**：打印机、「标签纸」、「确定打印」「干跑」按钮都是同一组。
  在这个页签时，下方预览换成文件第一页，汇总写「几页 × 几份」和文件怎么落到纸上。
- 纸张**一律用「标签纸」**（机器里装着的就是那一种纸）。PDF 页面尺寸和它不同就**等比缩放居中**
  （比如 100 × 100 的快递面单 PDF 打到 60 × 30 的纸上，两侧留白、不拉伸）；尺寸相同就是原样。
  ERP 的标签 PDF 是 90 × 50，打它之前把「标签纸」选成 90 × 50（快捷项里有）。
  打印后这组设置记进 `config.json`，下次打开还是它。
- 页面方向与纸张不一致时**自动旋转 90°**，横版标签打竖装的纸不会被压扁。
- 一样支持「干跑」先出 PNG 核对。上限 600 页/次。
- 依赖 `pypdfium2`（已加进 `pyproject.toml`，下次 `run.bat` 启动时 `uv sync`
  会自动装上）。

## 从 ERP 直接打印（打印桥）

ERP 的标签页（Items → 🏷️ Labels）可以直接把标签发到这台打印机，**不用打开
本工具的网页**。原理：本服务每 3 秒出站轮询一次 ERP 的打印队列（不开任何
入站端口），顺便上报本机打印机列表 —— ERP 页面上的打印机下拉框就是这么来的。

开启方法（一次性）：

1. 在 ERP 的运营设置里找到 `print_bridge_token`，复制它的值。
2. 编辑本目录的 `config.json`，把 `erp` 块改成：
   ```json
   "erp": { "enabled": true, "url": "https://erp.posterityindustries.com",
            "token": "<粘贴 token>", "name": "office", "poll_seconds": 3 }
   ```
3. 重启服务（`run.bat` / `run-lan.bat`，或 `Restart-ScheduledTask -TaskName "SKU Barcode Printer"`）。

之后 ERP 标签页出现「🖨️ Print now」按钮（代理 60 秒内有心跳才显示）。
打印失败会回报到 ERP 页面上；本地日志看控制台或 `logs/service.log` 里的
`[erp-bridge]` 行。

## 共享给局域网其他主机

双击 **`run-lan.bat`**（而不是 `run.bat`），服务会监听 `0.0.0.0`，控制台里会列出别人该访问的地址，例如 `http://192.168.5.10:5000`。

其他主机**不需要装 CT-428D 驱动**，任何有浏览器的设备（Windows / Mac / 手机 / 平板）打开地址就能用，打印动作全部在接着打印机的这台机器上执行。

连不上就是防火墙拦了入站，用**管理员** PowerShell 放行一次（只需一次）：

```powershell
New-NetFirewallRule -DisplayName "SKU Barcode Printer" -Direction Inbound -Protocol TCP -LocalPort 5000 -Action Allow
```

> ⚠️ 开放后同一网段的任何人打开这个地址都能直接让打印机出纸。这个服务没有登录验证，只适合放在可信的办公内网。用完关掉窗口即可停止；平时用 `run.bat`（只监听本机）。

### 开机自动后台运行

给同事长期用的话，用**管理员** PowerShell 装成开机自启（无窗口、不弹浏览器、顺带把防火墙放行）：

```powershell
cd C:\Project\sku-barcode-printer
powershell -ExecutionPolicy Bypass -File .\install-autostart.ps1
```

装完立即启动并自检，成功后会打印同事该访问的地址。之后每次开机自动拉起。

| 命令 | 作用 |
|---|---|
| `.\install-autostart.ps1` | 当前用户登录时启动（默认，推荐） |
| `.\install-autostart.ps1 -AtStartup` | 开机即启动，以 SYSTEM 身份跑，不需要有人登录 |
| `.\install-autostart.ps1 -AutoReload` | 改代码后服务自动重启（见下） |
| `.\install-autostart.ps1 -NoFirewall` | 不动防火墙 |
| `Restart-ScheduledTask -TaskName "SKU Barcode Printer"` | 手动重启服务 |
| `.\uninstall-autostart.ps1` | 卸载（删任务 + 关端口） |

后台服务的输出会写到 `logs/service.log`，服务起不来时先看这个。

### 改代码自动重新部署

加 `--reload`（后台服务用 `-AutoReload`）：

| 改了什么 | 生效方式 |
|---|---|
| `app.py` / `ocr.py` / `label.py` / `printing.py` / `learned.py` / `skudb.py` | **自动重启服务**，几秒后生效 |
| `config.json` / `learned.json` / `skus.db` | **不监视**（故意的）。打印是先存配置、打完再记学习结果，监视它们会让 reloader 在出纸中途杀掉进程，spooler 只收到 0 字节的残废作业。这两个文件本来就是每次现读现用 |
| `templates/*.html`、`static/*.js`、`static/*.css` | **不用重启**，浏览器刷新即可（静态文件已关缓存） |

```powershell
# 开发时手动跑
.venv\Scripts\python.exe app.py --reload
```

> `--reload` 只借用 Werkzeug 的文件监视器，**没有开 debug 模式** —— Werkzeug 的调试器能从网页执行任意代码，对局域网开放时绝不能开。
>
> 保存了带语法错误的文件时，服务会停在错误状态直到你改对；后台跑的时候没有控制台，报错在 `logs/service.log` 里。

> `-AtStartup` 用 SYSTEM 身份运行在 session 0，个别厂商驱动在该会话下打印会有问题。默认的「登录时启动」打印最稳，代价是这台机器需要保持登录状态。先用默认的，真不行再换 `-AtStartup`。
>
> 这两个 .ps1 是 **UTF-8 with BOM** 编码 —— Windows PowerShell 5.1 读脚本默认按 ANSI 解码，没 BOM 的话里面的中文会让脚本语法报错。编辑时注意别把 BOM 弄丢。

## 在 gw（Linux）上跑

gw 上没有厂商驱动，也不装 CUPS。`printing_tspl.py` 把同一张 203dpi 位图包成 TSPL2
命令直接写 `/dev/usb/lp0`（内核 `usblp` 自动认，打印机自报 `CMD:TSPL2`）。命令头
（`GAP 3.48 mm`、`DENSITY 10`、`DIRECTION 1,0` 等）是从 Windows 驱动的真实输出照抄的，
所以出纸和以前一样；来由在该文件头注释，`uv run python checks/tspl_capture.py`
拿截获的驱动输出逐字节对照。

| 项 | 值 |
|---|---|
| 目录 | `/home/tomato/sku-barcode-printer`（git 工作目录，`config.json` / `learned.json` 从 office 拷来） |
| 服务 | `sku-printer.service`（本仓根目录那份，装在 `/etc/systemd/system/`），监听 `0.0.0.0:5000` |
| 重启 | `sudo systemctl restart sku-printer`（**没开 reloader**，`git pull` 之后要手动重启） |
| 日志 | `journalctl -u sku-printer -f`（不写 `logs/service.log`） |
| 权限 | 设备是 `root:lp 0660`，服务靠 unit 里的 `SupplementaryGroups=lp` 拿写权限 |
| 字体 | `fonts-dejavu-core`（SKU 文字，替 Consolas Bold）、`fonts-wqy-zenhei`（中文，替黑体） |
| OCR 的系统库 | `libgl1`。rapidocr 拉的是非 headless 的 `opencv-python`，`import cv2` 要 `libGL.so.1`；gw 没有图形界面，缺它时 `/api/ocr` 回 500「OCR 失败: libGL.so.1: cannot open shared object file」，打印不受影响（2026-11-13 装上）。验法：`ldd .venv/lib/python3.13/site-packages/cv2/cv2*.so \| grep "not found"` 应为空 |
| A4 打印机 | EPSON L1350（无线，192.168.5.15），CUPS 队列 `EPSON-L1350`，IPP Everywhere 免驱动（`lpadmin -m everywhere`；Debian 的 ESC/P-R 驱动里没有这个型号）。`printing_cups.py` |

**A4 打印机（2026-11-11 起）**：打印机下拉里 CUPS 队列排在标签机后面，默认打印机永远是标签机。
- SKU 标签选它：一张标签一页 A4、按标签实际尺寸居中（同一张 203dpi 图贴在 A4 上存成 PDF）。
- 「打印文件」选它：原文件交给 CUPS，按文件自身尺寸印在 A4 上；页签里的「纸张」只管标签机。
- ERP 打印桥**不报**它（`kind=office`），ERP 那边只能选标签机。
- 「回退一张纸」对它报错（只对标签机有用）。

和 Windows 的差别：

- 打印机列表只列 `/dev/usb/lp*` 里说 TSPL 的设备，名字拼成「厂商 型号」=
  `CTPrinter CT-428D`，和 Windows 的队列名相同，配置与 ERP 记住的打印机名都不用改。
- 没有驱动预设纸张，纸张下拉只剩快捷尺寸和自定义。
- SKU 文字换成 DejaVu Sans Mono Bold，字比 Consolas 宽，同一个 SKU 字号会小一点；条码不受影响。
- 没有 spooler：打印出错直接报在页面上，不会有"作业卡在队列里"这回事。
- `.bat` / `.ps1` 在这台用不上。

## 默认设置

| 项 | 值 |
|---|---|
| 打印机 | CTPrinter CT-428D（203 dpi，8 点/mm） |
| 标签纸 | **自定义 60 × 30 mm**（驱动预设里没有 60×30，走 `DMPAPER_USER`；60×30 → 480 × 240 点） |
| 快捷尺寸 | 下拉里直接给了 60×30 / 90×50 / 100×100 / 100×150 mm，都走自定义尺寸，选完还能改宽高 |
| 标签内容 | Code128 条码 + 下方 SKU 文字 |
| 数量语义 | 数量 N = 打 N 张相同标签 |

勾选「只印文字（不生成条码）」后不再生成条码，直接把 SKU 文字按能放下的最大字号居中印在标签上；此时条码相关的版式控件全部失效，只有左右留白和旋转仍起作用。

**中文标签**：文字里出现汉字（或任何 Consolas/Arial 没有的字形）时自动换用黑体
（依次尝试 SimHei → 微软雅黑 Bold → 等线 → 宋体），纯 ASCII 的仍走 Consolas Bold，
外观不变。**中文只能走「只印文字」** —— Code128 本身只编 ASCII，不勾这个开关直接打
中文会报错并提示你去勾它。

「文字纵向铺满」滑块只在这个模式下可用：`自动` = 保持字形比例（只按宽度撑满），拉到 N% = 把字上下拉伸到占标签高度的 N%，100% 就是铺满整张纸。数值越大字被拉得越扁长，窄高标签或旋转 90° 时拉到 100% 会明显变形，看预览决定。

版式比例（占标签宽/高的百分比，换纸张尺寸自动等比）：左右留白各 10%、条码距顶 8.5%、条码高 67%、文字高 12%。

## 文件

| 文件 | 作用 |
|---|---|
| `app.py` | Flask 路由。`/api/ocr` 只识别；**只有 `/api/print` 会出纸** |
| `ocr.py` | 图片走 RapidOCR → 按 y 聚类还原表格行；文本走 `parse_text()` 逐行拆词。两条路共用同一套 SKU 归一化与易混字纠正。**无状态** —— 字典通过 `lookup=` 注入 |
| `learned.py` | 从人工修正里学到的字典 / 映射 / 黑名单，见下节 |
| `skudb.py` | SKU 库（SQLite），「SKU 库」页签的数据源，见「SKU 库」 |
| `label.py` | 按 203dpi 出 1:1 像素位图，条码模块按**整数像素**逐条画，不让驱动缩放 |
| `printing.py` | 枚举打印机与驱动纸张、DEVMODE 设自定义尺寸、GDI 逐页打印；非 Windows 时转给下一行 |
| `printing_tspl.py` | Linux 后端：发现 `/dev/usb/lp*` 上的 TSPL 打印机，位图包成 TSPL 直写 |
| `checks/tspl_capture.py` | 拿 `checks/fixtures/` 里截获的 Windows 驱动输出，逐字节验 TSPL 编码（自带对照） |
| `sku-printer.service` | gw 上的 systemd unit |
| `config.json` | 记住上次的打印机 / 纸张 / 版式，每次打印后自动更新。**不进 git**（含 ERP 打印桥的 token）—— 新机器上从 `config.example.json` 复制一份；不复制也能跑，代码里有同样的默认值 |
| `learned.json` | 学到的东西落盘在这里。不进 git（是客户 SKU 清单），删了就从头学 |
| `skus.db` | SKU 库落盘在这里。不进 git，理由同上；删了会从 `learned.json` 重新导入 |

## 会自己纠错

图片 OCR 认错的 SKU，你在核对列表里改一次，它就记住了。**只作用在图片路径上** —— 粘贴的文本是人敲的，没有形近错，拿字典去动它只会帮倒忙。

学三种东西：

| 记什么 | 什么时候记 | 下次怎么用 |
|---|---|---|
| SKU 字典 | 打印成功（要攒够 2 次）；你亲手敲进去的（立刻算数） | 大小写回正、合并裁决、模糊匹配的目标 |
| 错 → 对 映射 | 你把 X 改成 Y 时 | 见 X 直接改成 Y，行上标出来可撤销 |
| 误识别黑名单 | 删行时你选了「这是误识别」 | 下次照样列出来，只是**默认不勾选并标灰** |

分级套用，一条准绳：**能确定的才自动改，靠猜的只给建议**。

- 精确映射命中、大小写回正 → 直接改，行下面标一行小字，可撤销
- 形近模糊匹配（只认 `O↔0 I↔1 S↔5 B↔8 Z↔2 G↔6`，且段结构完全一致、候选唯一）→ **只给建议**。把一个真的新 SKU 静默改成旧的，那种错没人会发现
- 含空格的 SKU（`MCH-SS V2`）：空格既可能是列分隔符又可能是 SKU 内字符，没有规则分得干净，只认字典命中。第一次它会出成 `MCH-SS`，你改一次，以后就对了

自愈：你把 X 改成 Y 时，X 如果在字典里会被就地删掉（它刚被你判为错的），免得模糊匹配一直拿它当靠拢目标。出了岔子，页面底部「学到的纠错」面板能看能删，或者直接编辑 `learned.json`。

## 调整识别规则

`config.json` 里：

```jsonc
"ocr": {
  "sku_regex": "^[A-Za-z0-9]{2,6}(-[A-Za-z0-9]{1,6}){1,6}$",  // SKU 长什么样
  "min_score": 0.85                                            // 低于此分数的行标黄
}
```

默认认 2~7 段、段间 `-` 的写法，大小写都收（`UK4-SS-mn-GL-AU-BK` 里的 `mn` 就是小写），覆盖 `EU-PRO-01`、`PHC-MIN-MP-WLB`、`SS-GLA-AUR-GLA-WLB-WH`、`A3-SS-DW-AUR-GLA-WLB` 这几类。

> **空格不要加进这个正则。** 加了之后描述列里的 `Wall Lamp`（4 + 4 字符）也成了合法 SKU，误判面直接炸开。含空格的 SKU 靠字典命中，见上一节。
>
> 这份 `sku_regex` 会**覆盖** `ocr.py` 里的默认值 —— 只改代码不改这里是不生效的。

## 常见问题

- **装纸后吐出一张空白**：合上打印头时机器会走一张去找间隙。点打印设置里的「回退一张纸」把它收回去，
  下一张就印在它上面。退的距离 = 「标签纸」的高度 + 间隙 3.48 mm（TSPL `BACKFEED`），所以先把纸张选对。
  只在 gw 上有（Windows 中间隔着驱动，GDI 没有退纸）。
- **一次走好几张纸 / 标签串位**：标签间隙没校准。到「打印机属性 → 首选项」做一次介质/间隙校准。
- **扫码枪读不出**：看预览下方提示。若显示「窄条只有 1 点」，说明 SKU 太长挤在小标签上，减小左右留白或换更宽的标签纸。
- **条码没占满标签宽度**：模块宽默认取整数个热敏点（203dpi 下 1 点 = 0.125mm），凑不满就宁可留白。「模块宽 px」选 **填满** 会启用小数模块宽（每个模块边界单独取整到点阵，条依然是纯黑纯白，不是位图拉伸），把条码撑到可用宽度。但小数宽会让每根条在 ⌊m⌋/⌈m⌉ 点之间抖动，实测（zxing-cpp 回解 203dpi 位图）模块宽 ≤1.8 点时全线读不出、≥1.85 点才稳定，所以低于 2 点会自动退回整数宽并在预览下方说明原因 —— 20 字符以上的 SKU 在 60mm 纸上就属于这种情况，真要填满只能换更宽的纸或缩短 SKU。
- **识别不到行**：表格截图太小或太糊时放大再截；也可以点「+ 手动加一行」自己敲。
- **想换标签尺寸**：右侧「标签纸」下拉里有 60×30 / 90×50 / 100×100 / 100×150 四个快捷项、驱动内置的 14 种预设，或选「自定义尺寸」自己填 mm。
- **纸宽超过 104mm**：CT-428D 的打印头是 833 点 @203dpi = **104.2mm**，比这更宽的纸超出的部分永远印不上，自己填宽度超过这个数时预览下方会警告。大标签用 100×150（宽 100 高 150），别填成 150×100。
