const $ = (id) => document.getElementById(id);

let rows = [];          // {selected, description, sku, qty, low_confidence, qty_detected}
let activeIndex = 0;
let papers = [];
let previewTimer = null;
let lastRaw = null;     // 上次 OCR 的原文，切语言时要重画折叠标题

// --------------------------------------------------------------------------- //
// 中英切换
// --------------------------------------------------------------------------- //

let lang = localStorage.getItem('sku_lang') || 'zh';

function t(key, ...args) {
  const v = (I18N[lang] || I18N.zh)[key];
  if (v === undefined) return key;
  return typeof v === 'function' ? v(...args) : v;
}

function applyI18n() {
  document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
  document.title = t('app.title');
  document.querySelectorAll('[data-i18n]').forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll('[data-i18n-html]').forEach((el) => {
    el.innerHTML = t(el.dataset.i18nHtml);
  });
  document.querySelectorAll('[data-i18n-ph]').forEach((el) => {
    el.placeholder = t(el.dataset.i18nPh);
  });
  $('lang').textContent = t('app.langbtn');

  // 动态生成的部分要重画
  syncRangeLabels();  // 「自动」这类值标签不是 data-i18n，得自己刷
  renderPaperOptions();
  renderPrinterOptions();
  renderTable();
  renderDb();
  if (lastRaw) $('rawSummary').textContent = t('raw.summary', lastRaw.count);
}

function switchLang() {
  lang = lang === 'zh' ? 'en' : 'zh';
  localStorage.setItem('sku_lang', lang);
  applyI18n();
}

// --------------------------------------------------------------------------- //
// 参数
// --------------------------------------------------------------------------- //

function layout() {
  return {
    rotate: parseInt($('rotate').value, 10),
    margin_x_mm: parseFloat($('marginx').value),
    barcode_top_pct: parseFloat($('btop').value),
    barcode_height_pct: parseFloat($('bheight').value),
    text_height_pct: parseFloat($('theight').value),
    module_px: parseInt($('modulepx').value, 10),
    show_text: $('showtext').checked,
    text_only: $('textonly').checked,
    text_fill_pct: parseFloat($('textfill').value),
  };
}

// 只印文字时，条码相关的控件全部失效 —— 留着能点会让人以为还有条码
const BARCODE_ONLY = [['lbModule', 'modulepx'], ['lbTop', 'btop'], ['lbBh', 'bheight'],
                      ['lbTh', 'theight'], ['lbShow', 'showtext']];

function syncTextOnly() {
  const on = $('textonly').checked;
  BARCODE_ONLY.forEach(([lb, inp]) => {
    $(lb).classList.toggle('off', on);
    $(inp).disabled = on;
  });
  $('lbFill').classList.toggle('off', !on);  // 纵向铺满只在只印文字时有意义
  $('textfill').disabled = !on;
}

// 常用尺寸快捷项。驱动预设里没有这些，都走 DMPAPER_USER 自定义尺寸
const QUICK_SIZES = [[60, 30], [90, 50], [100, 100], [100, 150]];

function paperSpec() {
  const sel = $('paper').value;
  if (sel === 'custom' || sel.startsWith('q')) {
    return { mode: 'custom', paper_id: 256, width_mm: parseFloat($('wmm').value), height_mm: parseFloat($('hmm').value) };
  }
  const p = papers[parseInt(sel, 10)];
  return { mode: 'preset', paper_id: p.id, width_mm: p.width_mm, height_mm: p.height_mm };
}

// --------------------------------------------------------------------------- //
// 初始化
// --------------------------------------------------------------------------- //

let printerList = [];
let printerRes = {};   // 驱动报的 dpi 与可打印点数，用来判断纸宽有没有超出打印头

function renderPrinterOptions() {
  const sel = $('printer');
  if (!printerList.length) return;
  const keep = sel.value;
  sel.innerHTML = '';
  printerList.forEach((p) => {
    const o = document.createElement('option');
    o.value = p.name;
    o.textContent = p.name + (p.is_default ? t('printer.default') : '');
    sel.appendChild(o);
  });
  if (keep) sel.value = keep;
}

async function init() {
  // 先让界面能用，再去拿数据。/api/printers 冷启动时服务端要现场 import
  // pywin32 和 PIL，要等一会儿；把 bind() 排在它后面，这段时间里页面看着
  // 正常其实点什么都没反应。
  bind();
  applyI18n();
  refreshSummary();

  // 这两个请求互不依赖，并发发省一个来回
  const [cfg, info] = await Promise.all([
    fetch('/api/config').then((r) => r.json()),
    fetch('/api/printers').then((r) => r.json()),
  ]);
  applyConfig(cfg);

  printerList = info.printers || [];
  renderPrinterOptions();
  const sel = $('printer');
  sel.value = cfg.printer && printerList.some((p) => p.name === cfg.printer) ? cfg.printer : info.selected;
  await loadPapers(sel.value, cfg);

  refreshPreview();
}

function applyConfig(cfg) {
  const L = cfg.layout || {};
  if (L.rotate !== undefined) $('rotate').value = L.rotate;
  if (L.module_px !== undefined) $('modulepx').value = L.module_px;
  if (L.margin_x_mm !== undefined) $('marginx').value = L.margin_x_mm;
  if (L.barcode_top_pct !== undefined) $('btop').value = L.barcode_top_pct;
  if (L.barcode_height_pct !== undefined) $('bheight').value = L.barcode_height_pct;
  if (L.text_height_pct !== undefined) $('theight').value = L.text_height_pct;
  if (L.show_text !== undefined) $('showtext').checked = !!L.show_text;
  if (L.text_only !== undefined) $('textonly').checked = !!L.text_only;
  if (L.text_fill_pct !== undefined) $('textfill').value = L.text_fill_pct;
  const P = cfg.paper || {};
  if (P.width_mm) $('wmm').value = P.width_mm;
  if (P.height_mm) $('hmm').value = P.height_mm;
  syncRangeLabels();
  syncTextOnly();
}

function renderPaperOptions() {
  const sel = $('paper');
  const keep = sel.value;
  sel.innerHTML = '';
  const custom = document.createElement('option');
  custom.value = 'custom';
  custom.textContent = t('paper.custom');
  sel.appendChild(custom);
  QUICK_SIZES.forEach(([w, h], i) => {
    const o = document.createElement('option');
    o.value = 'q' + i;
    o.textContent = t('paper.quick', w, h);
    sel.appendChild(o);
  });
  papers.forEach((p, i) => {
    const o = document.createElement('option');
    o.value = String(i);
    o.textContent = `${p.name}  —  ${p.width_mm} × ${p.height_mm} mm`;
    sel.appendChild(o);
  });
  if (keep) sel.value = keep;
}

async function loadPapers(printer, cfg) {
  const r = await (await fetch('/api/papers?printer=' + encodeURIComponent(printer))).json();
  papers = r.papers || [];
  printerRes = r.resolution || {};
  const sel = $('paper');
  renderPaperOptions();
  // 驱动预设里没有 60×30 这类尺寸，默认落在自定义/快捷项上
  const wantPreset = cfg && cfg.paper && cfg.paper.mode === 'preset';
  if (wantPreset) {
    const idx = papers.findIndex((p) => p.id === cfg.paper.paper_id);
    sel.value = idx >= 0 ? String(idx) : 'custom';
  } else {
    // 上次存的自定义尺寸正好等于某个快捷项时，直接选中它
    const P = (cfg && cfg.paper) || {};
    const qi = QUICK_SIZES.findIndex(([w, h]) => w === P.width_mm && h === P.height_mm);
    sel.value = qi >= 0 ? 'q' + qi : 'custom';
  }
  onPaperChange();
}

function onPaperChange() {
  const v = $('paper').value;
  const quick = v.startsWith('q');
  // 快捷项也是自定义尺寸，宽高框留着可见 —— 选完还能接着微调
  $('customSize').classList.toggle('hidden', !(v === 'custom' || quick));
  if (quick) {
    const [w, h] = QUICK_SIZES[parseInt(v.slice(1), 10)];
    $('wmm').value = w;
    $('hmm').value = h;
  } else if (v !== 'custom') {
    const p = papers[parseInt(v, 10)];
    $('wmm').value = p.width_mm;
    $('hmm').value = p.height_mm;
  }
  refreshPreview();
}

// --------------------------------------------------------------------------- //
// 图片上传 → 自动识别
// --------------------------------------------------------------------------- //

function setStatus(msg, kind) {
  const el = $('status');
  el.className = 'status' + (kind ? ' ' + kind : '');
  el.textContent = msg;
  el.classList.toggle('hidden', !msg);
}

async function handleImage(blob) {
  const url = URL.createObjectURL(blob);
  $('shotImg').src = url;
  $('shot').classList.remove('hidden');
  $('drop').classList.add('hidden');

  setStatus(t('ocr.working'));
  const fd = new FormData();
  fd.append('image', blob, 'shot.png');
  try {
    const res = await fetch('/api/ocr', { method: 'POST', body: fd });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || 'OCR failed');
    rows = (data.rows || []).map((r) => ({
      selected: r.selected !== false,
      description: r.description || '',
      sku: r.sku,
      qty: r.qty,
      low_confidence: r.low_confidence,
      qty_detected: r.qty_detected,
      // 学习用：source='ocr' 的行改了才值得记一条 错→对 映射，
      // 人敲的和粘贴的没有形近错，只把结果收进白名单。
      source: 'ocr',
      origin: r.sku,
      junk: !!r.junk,
      corrected_from: r.corrected_from || null,
      suggestion: r.suggestion || null,
    }));
    activeIndex = 0;
    renderTable();

    // OCR 原文留给排查用：漏行时一眼能看出是没读到、还是读到了没配上
    lastRaw = { count: data.raw_count, texts: data.raw_texts || [] };
    $('rawSummary').textContent = t('raw.summary', lastRaw.count);
    $('rawText').textContent = lastRaw.texts.join('\n');
    $('rawWrap').classList.remove('hidden');
    $('rawWrap').open = false;

    if (!rows.length) {
      setStatus(t('ocr.none', data.raw_count), 'err');
    } else {
      const warn = rows.filter((r) => r.low_confidence || !r.qty_detected).length;
      setStatus(warn ? t('ocr.warn', rows.length, warn) : t('ocr.ok', rows.length), warn ? '' : 'ok');
    }
  } catch (e) {
    setStatus(String(e.message || e), 'err');
  }
}

// --------------------------------------------------------------------------- //
// 文本粘贴 → 解析
// --------------------------------------------------------------------------- //

async function parseText() {
  const text = $('text').value;
  if (!text.trim()) { setStatus(t('txt.empty'), 'err'); return; }
  setStatus(t('txt.working'));
  try {
    const res = await fetch('/api/parse-text', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, raw: $('rawmode').checked }),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || 'Parse failed');
    // 原文模式下不写数量是常态，别整片标黄
    const raw = $('rawmode').checked;
    rows = (data.rows || []).map((r) => ({
      selected: true,
      description: r.description || '',
      sku: r.sku,
      qty: r.qty,
      low_confidence: false,
      qty_detected: raw ? true : r.qty_detected,
      source: 'text',
      origin: r.sku,
      junk: false,
      corrected_from: null,
      suggestion: null,
    }));
    activeIndex = 0;
    renderTable();

    const bad = (data.unparsed || []).length;
    const noQty = rows.filter((r) => !r.qty_detected).length;
    if (!rows.length) {
      setStatus(t('txt.none'), 'err');
    } else if (bad) {
      setStatus(t('txt.skipped', rows.length, bad, data.unparsed.join('\n')), '');
    } else {
      setStatus(noQty ? t('txt.noqty', rows.length, noQty) : t('txt.ok', rows.length), noQty ? '' : 'ok');
    }
  } catch (e) {
    setStatus(String(e.message || e), 'err');
  }
}

// 当前页签。下方「打印设置 / 预览 / 打印」是共用的：在「打印文件」页签时打的是那份文件，
// 其余页签打的是核对列表
let activeTab = 'img';
const fileMode = () => activeTab === 'file';

function switchTab(name) {
  activeTab = name;
  refreshSummary();
  refreshPreview();
  document.querySelectorAll('.tab').forEach((el) => el.classList.toggle('on', el.dataset.tab === name));
  $('paneImg').classList.toggle('hidden', name !== 'img');
  $('paneTxt').classList.toggle('hidden', name !== 'txt');
  $('paneFile').classList.toggle('hidden', name !== 'file');
  $('paneDb').classList.toggle('hidden', name !== 'db');
  if (name === 'db') {
    if (!dbItems) loadDb();
    $('dbQ').focus();
  }
}

// --------------------------------------------------------------------------- //
// SKU 库：自己维护的 SKU 清单。搜出来勾选，加进核对列表，数量在核对列表里改、照常走「确定打印」
// --------------------------------------------------------------------------- //

let dbItems = null;          // null = 还没拉过；真打印成功后置回 null（可能自动入了库），下次切过来重拉
const dbPicked = new Set();  // 勾选跨搜索保留：搜 A 勾两条、再搜 B 勾一条，一起加
let dbEditing = null;        // 正在改的那条 SKU（行内变成输入框）
const DB_SHOW = 200;         // 一屏最多列这么多，再多就让人多敲几个字

// 所有库操作都回整份清单，成功就换掉 dbItems 重画；失败把原因写在面板底下
async function dbCall(method, url, body) {
  const el = $('dbResult');
  el.className = 'result';
  el.textContent = '';
  try {
    const opt = { method, headers: { 'Content-Type': 'application/json' } };
    if (body) opt.body = JSON.stringify(body);
    const d = await (await fetch(url, opt)).json();
    if (d.items) dbItems = d.items;
    if (!d.ok) throw new Error(d.error || 'Failed');
    return d;
  } catch (e) {
    el.className = 'result err';
    el.textContent = String(e.message || e);
    return null;
  } finally {
    renderDb();
  }
}

function loadDb() {
  $('dbInfo').textContent = t('db.loading');
  return dbCall('GET', '/api/skus');
}

function dbHits() {
  const q = $('dbQ').value.trim().toLowerCase();
  return q
    ? dbItems.filter((s) => s.sku.toLowerCase().includes(q) || s.description.toLowerCase().includes(q))
    : dbItems;
}

function renderDb() {
  const ul = $('dbList');
  syncDbPick();
  if (!dbItems) { ul.innerHTML = ''; return; }
  const q = $('dbQ').value.trim();
  const hits = dbHits();
  const shown = hits.slice(0, DB_SHOW);

  ul.innerHTML = shown.map((s) => (s.sku === dbEditing ? `
    <li class="editing">
      <input type="text" class="esku" value="${esc(s.sku)}" data-k="sku">
      <input type="text" class="edesc" value="${esc(s.description)}" data-k="description"
             placeholder="${esc(t('db.descph'))}">
      <button class="link save">${esc(t('db.save'))}</button>
      <button class="link cancel">${esc(t('db.cancel'))}</button>
    </li>` : `
    <li class="${dbPicked.has(s.sku) ? 'on' : ''}">
      <label>
        <input type="checkbox" data-sku="${esc(s.sku)}" ${dbPicked.has(s.sku) ? 'checked' : ''}>
        <span class="dsku">${esc(s.sku)}</span>
        ${s.description ? `<span class="ddesc">${esc(s.description)}</span>` : ''}
      </label>
      <button class="link ed" data-sku="${esc(s.sku)}" title="${esc(t('db.edit'))}">✎</button>
      <button class="link lx" data-sku="${esc(s.sku)}" title="${esc(t('db.del'))}">×</button>
    </li>`)).join('');

  const info = $('dbInfo');
  const quick = $('dbQuickAdd');
  quick.classList.add('hidden');
  if (!dbItems.length && !q) info.textContent = t('db.none');
  else if (!hits.length) {
    info.textContent = t('db.nomatch', q);
    // 搜不到的那串，一键收进库
    quick.textContent = t('db.quickadd', q);
    quick.classList.remove('hidden');
  } else if (hits.length > shown.length) info.textContent = t('db.more', hits.length, shown.length);
  else info.textContent = t('db.count', hits.length, dbItems.length);

  ul.querySelectorAll('input[type=checkbox]').forEach((cb) => cb.addEventListener('change', () => {
    if (cb.checked) dbPicked.add(cb.dataset.sku); else dbPicked.delete(cb.dataset.sku);
    cb.closest('li').classList.toggle('on', cb.checked);
    syncDbPick();
  }));
  ul.querySelectorAll('.ed').forEach((b) => b.addEventListener('click', () => {
    dbEditing = b.dataset.sku;
    renderDb();
    const inp = ul.querySelector('li.editing .esku');
    if (inp) inp.focus();
  }));
  ul.querySelectorAll('.lx').forEach((b) => b.addEventListener('click', () => deleteDbSku(b.dataset.sku)));

  const ed = ul.querySelector('li.editing');
  if (ed) {
    const save = () => saveDbEdit(ed.querySelector('.esku').value, ed.querySelector('.edesc').value);
    ed.querySelector('.save').addEventListener('click', save);
    ed.querySelector('.cancel').addEventListener('click', () => { dbEditing = null; renderDb(); });
    ed.querySelectorAll('input').forEach((inp) => inp.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); save(); }
      if (e.key === 'Escape') { dbEditing = null; renderDb(); }
    }));
  }
}

function syncDbPick() {
  $('dbPick').textContent = t('db.pick', dbPicked.size);
  $('dbPick').disabled = dbPicked.size === 0;
}

async function saveDbEdit(sku, description) {
  const old = dbEditing;
  const d = await dbCall('PUT', '/api/skus', { old_sku: old, sku: sku.trim(), description });
  if (!d) return;  // 撞名 / 空 SKU：留在编辑态，原因写在底下
  dbEditing = null;
  if (dbPicked.delete(old)) dbPicked.add(sku.trim());  // 改名时勾选跟着走
  renderDb();
}

async function deleteDbSku(sku) {
  if (!confirm(t('db.delConfirm', sku))) return;
  if (await dbCall('DELETE', '/api/skus', { sku })) dbPicked.delete(sku);
  renderDb();
}

async function quickAddDb() {
  const sku = $('dbQ').value.trim();
  if (!sku) return;
  if (await dbCall('POST', '/api/skus', { sku, description: '' })) {
    $('dbResult').className = 'result ok';
    $('dbResult').textContent = t('db.added1', sku);
  }
}

async function importDb() {
  const text = $('dbImportText').value;
  if (!text.trim()) return;
  const d = await dbCall('POST', '/api/skus/import', { text });
  if (!d) return;
  $('dbImportText').value = '';
  const el = $('dbResult');
  el.className = 'result ok';
  el.textContent = t('db.imported', d.added.length, d.skipped.length)
    + (d.bad.length ? '\n' + t('db.importBad', d.bad.join('\n')) : '');
}

function pickDbToList() {
  let added = 0;
  let already = 0;
  // 按库里的顺序加，不按勾选的先后 —— 面板里看到什么顺序，核对列表就是什么顺序
  dbItems.filter((s) => dbPicked.has(s.sku)).forEach((s) => {
    const dup = rows.find((r) => r.sku.trim() === s.sku);
    if (dup) { dup.selected = true; already += 1; return; }  // 已在列表里：勾上，数量不动
    rows.push({
      selected: true, description: s.description, sku: s.sku, qty: 1,
      low_confidence: false, qty_detected: true, source: 'db', origin: '',
      junk: false, corrected_from: null, suggestion: null,
    });
    added += 1;
  });
  if (added) activeIndex = rows.length - 1;
  dbPicked.clear();
  renderTable();
  renderDb();
  setStatus(already ? t('db.pickedDup', added, already) : t('db.picked', added), 'ok');
}

// --------------------------------------------------------------------------- //
// 打印文件（PDF / 图片）—— 比如 ERP 导出的二维码标签 PDF
// --------------------------------------------------------------------------- //

let pickedFile = null;   // File 对象；打印时原样再传给 /api/print-file
let pickedInfo = null;   // {pages, width_mm, height_mm}

async function handleDocFile(f) {
  if (!f) return;
  pickedFile = f;
  pickedInfo = null;
  const el = $('fileInfo');
  el.textContent = t('file.reading');
  $('dropFile').classList.add('hidden');
  $('fileReady').classList.remove('hidden');
  $('printResult').textContent = '';

  const fd = new FormData();
  fd.append('file', f, f.name);
  try {
    const res = await fetch('/api/file-info', { method: 'POST', body: fd });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || 'Failed');
    pickedInfo = data;
    $('filePreview').src = data.preview;
    el.textContent = data.width_mm
      ? t('file.info', f.name, data.pages, data.width_mm, data.height_mm)
      : t('file.infoimg', f.name);
  } catch (e) {
    el.textContent = String(e.message || e);
    pickedFile = null;
  }
  refreshSummary();
  refreshPreview();
}

// 选中的是 A4 普通打印机（gw 上 CUPS 里的 EPSON）？服务端给每台打印机标了 kind。
// 是的话「打印文件」把原文件交给它、用 A4，「标签纸」不起作用。
function isOfficePrinter() {
  const p = printerList.find((x) => x.name === $('printer').value);
  return !!(p && p.kind === 'office');
}

function fileCopies() {
  return Math.max(1, parseInt($('fileCopies').value || '1', 10));
}

// 文件会怎么落到纸上，一句话。下方预览底下和汇总里用
function fileFitText() {
  if (!pickedFile) return '';
  if (isOfficePrinter()) return t('file.fitA4');
  const p = paperSpec();
  if (!pickedInfo || !pickedInfo.width_mm) return t('file.fitImg', p.width_mm, p.height_mm);
  const same = (a, b) => Math.abs(a - b) < 0.6;
  const w = pickedInfo.width_mm, h = pickedInfo.height_mm;
  if ((same(w, p.width_mm) && same(h, p.height_mm)) || (same(w, p.height_mm) && same(h, p.width_mm))) {
    return t('file.fitSame', p.width_mm, p.height_mm);
  }
  return t('file.fit', w, h, p.width_mm, p.height_mm);
}

// 纸张一律用下方「打印设置」的「标签纸」（use_paper=1）：机器里装着的就是那一种纸。
// PDF 页面尺寸不同就等比缩放居中，方向不一致自动转 90°（服务端 printing 里做）。
async function submitFilePrint(dryRun) {
  if (!pickedFile) return;
  const copies = fileCopies();
  const pages = pickedInfo ? pickedInfo.pages : 1;
  const office = !dryRun && isOfficePrinter();
  const p = paperSpec();
  if (!dryRun) {
    const ok = office
      ? confirm(t('file.confirmA4', pickedFile.name, pages, copies, pages * copies, $('printer').value))
      : confirm(t('file.confirm', pickedFile.name, pages, copies, pages * copies,
                  $('printer').value, p.width_mm, p.height_mm));
    if (!ok) return;
  }

  const btn = dryRun ? $('dry') : $('print');
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = t(dryRun ? 'btn.drying' : 'btn.printing');
  const el = $('printResult');
  const fd = new FormData();
  fd.append('file', pickedFile, pickedFile.name);
  fd.append('printer', $('printer').value);
  fd.append('copies', String(copies));
  fd.append('dry_run', dryRun ? '1' : '0');
  fd.append('paper', JSON.stringify(p));
  fd.append('use_paper', '1');
  try {
    const res = await fetch('/api/print-file', { method: 'POST', body: fd });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || 'Failed');
    el.className = 'result ok';
    el.textContent = dryRun
      ? t('dry.done', data.files.length, data.total_labels, data.files.join('\n'))
      : office
        ? t('print.doneA4', pages * copies, $('printer').value)
        : t('print.done', data.printed, $('printer').value);
  } catch (e) {
    el.className = 'result err';
    el.textContent = String(e.message || e);
  } finally {
    btn.disabled = false;
    btn.textContent = old;
    refreshSummary();
  }
}

// --------------------------------------------------------------------------- //
// 表格
// --------------------------------------------------------------------------- //

function renderTable() {
  const tb = $('tbody');
  tb.innerHTML = '';
  rows.forEach((r, i) => {
    const tr = document.createElement('tr');
    if (r.low_confidence || !r.qty_detected) tr.classList.add('warn');
    if (r.junk) tr.classList.add('junk');
    if (i === activeIndex) tr.classList.add('active');

    // SKU 格下面挂一条小字：自动改了什么、或者有什么建议。
    // 建议永远只是建议 —— 把一个真的新 SKU 静默改成旧的，那种错没人会发现。
    let hint = '';
    if (r.corrected_from) {
      hint = `<div class="hint fixed">${esc(t('row.fixed', r.corrected_from))}
              <button class="link undo">${esc(t('row.undo'))}</button></div>`;
    } else if (r.suggestion) {
      hint = `<div class="hint sug">${esc(t('row.suggest', r.suggestion))}
              <button class="link take">${esc(t('row.take'))}</button></div>`;
    } else if (r.junk) {
      hint = `<div class="hint junked">${esc(t('row.junked'))}</div>`;
    }

    tr.innerHTML = `
      <td><input type="checkbox" ${r.selected ? 'checked' : ''} data-k="selected"></td>
      <td><input type="text" class="sku" value="${esc(r.sku)}" data-k="sku">${hint}</td>
      <td><input type="number" min="1" max="999" value="${r.qty}" data-k="qty"></td>
      <td><input type="text" class="desc" value="${esc(r.description)}" data-k="description"></td>
      <td><button class="del" title="${esc(t('row.del'))}">×</button></td>`;

    tr.querySelectorAll('input').forEach((inp) => {
      inp.addEventListener('input', () => {
        const k = inp.dataset.k;
        if (k === 'selected') r.selected = inp.checked;
        else if (k === 'qty') r.qty = Math.max(1, parseInt(inp.value || '1', 10));
        else r[k] = inp.value;
        if (k === 'sku' || k === 'qty') { r.low_confidence = false; r.qty_detected = true; }
        refreshSummary();
        if (k === 'sku') refreshPreview();
      });
      // 学习走 change 不走 input —— 否则每敲一个字符就上报一条半截的映射
      if (inp.dataset.k === 'sku') inp.addEventListener('change', () => { learnEdit(r); renderTable(); });
      inp.addEventListener('focus', () => { activeIndex = i; markActive(); refreshPreview(); });
    });

    const undo = tr.querySelector('.undo');
    if (undo) undo.addEventListener('click', () => {
      r.sku = r.corrected_from;       // 退回 OCR 原值；字典不动，要清就去管理面板
      r.corrected_from = null;
      renderTable();
    });
    const take = tr.querySelector('.take');
    if (take) take.addEventListener('click', () => {
      r.sku = r.suggestion;           // 你点了采纳 = 亲口确认，按人工修正记账
      r.suggestion = null;
      learnEdit(r);
      renderTable();
    });

    tr.querySelector('.del').addEventListener('click', () => {
      // 只有 OCR 出来的行才值得问 —— 手动加的、粘贴来的删掉没有学习意义
      if (r.source === 'ocr' && (r.sku || '').trim()) askDelete(tr, r, i);
      else dropRow(i);
    });
    tb.appendChild(tr);
  });
  $('empty').classList.toggle('hidden', rows.length > 0);
  refreshSummary();
  refreshPreview();
}

function dropRow(i) {
  rows.splice(i, 1);
  if (activeIndex >= rows.length) activeIndex = Math.max(0, rows.length - 1);
  renderTable();
}

function askDelete(tr, r, i) {
  // 「误识别」和「这单不打」在操作上分不开，只能当场问一句。
  // 猜错的代价不对称：把真 SKU 学成垃圾，以后会静默少打标签。
  tr.classList.add('asking');
  tr.innerHTML = `<td colspan="5" class="ask">
      <span>${esc(t('row.ask', r.sku.trim()))}</span>
      <button class="link a-junk">${esc(t('row.ask.junk'))}</button>
      <button class="link a-once">${esc(t('row.ask.once'))}</button>
      <button class="link a-cancel">${esc(t('row.ask.cancel'))}</button>
    </td>`;
  tr.querySelector('.a-junk').addEventListener('click', () => {
    learn({ kind: 'junk', text: r.sku.trim() });
    dropRow(i);
  });
  tr.querySelector('.a-once').addEventListener('click', () => dropRow(i));
  tr.querySelector('.a-cancel').addEventListener('click', renderTable);
}

// --------------------------------------------------------------------------- //
// 学习上报
// --------------------------------------------------------------------------- //

function learn(payload) {
  // 学习失败绝不能挡住打印这条主路，记在控制台就够了
  return fetch('/api/learn', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).catch((e) => console.warn('learn failed', e));
}

function learnEdit(r) {
  const val = (r.sku || '').trim();
  if (!val || val === r.origin) return;
  if (r.source === 'ocr' && r.origin) {
    learn({ kind: 'fix', wrong: r.origin, right: val });   // OCR 错了，你改对了
  } else {
    learn({ kind: 'sku', sku: val });                      // 人敲的，只进白名单
  }
  r.origin = val;          // 连着改第二次时，别把中间态当成 OCR 原值记进去
  r.corrected_from = null;
  r.suggestion = null;
}

async function loadLearned() {
  const box = $('learnedBody');
  box.textContent = t('learned.loading');
  try {
    renderLearned(await (await fetch('/api/learned')).json());
  } catch (e) {
    box.textContent = String(e.message || e);
  }
}

function renderLearned(d) {
  const box = $('learnedBody');
  const sec = (title, items, li) => (items.length
    ? `<div class="lsec"><h4>${esc(title)} (${items.length})</h4><ul>${items.map(li).join('')}</ul></div>`
    : `<div class="lsec"><h4>${esc(title)}</h4><p class="lempty">${esc(t('learned.none'))}</p></div>`);
  const del = (kind, key) => `<button class="link lx" data-kind="${kind}" data-key="${esc(key)}">×</button>`;

  box.innerHTML =
    sec(t('learned.skus'), d.skus, (s) => `<li class="${s.active ? '' : 'pending'}">
        <code>${esc(s.sku)}</code>
        <span class="lmeta">${s.active ? '' : esc(t('learned.pending', d.threshold - s.n))}</span>
        ${del('skus', s.sku)}</li>`) +
    sec(t('learned.fixes'), d.fixes, (f) => `<li>
        <code>${esc(f.wrong)}</code> → <code>${esc(f.right)}</code>
        ${del('fixes', f.wrong)}</li>`) +
    sec(t('learned.junk'), d.junk, (j) => `<li>
        <code>${esc(j.text)}</code>
        ${del('junk', j.text)}</li>`);

  box.querySelectorAll('.lx').forEach((b) => b.addEventListener('click', async () => {
    const res = await fetch('/api/learned', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: b.dataset.kind, key: b.dataset.key }),
    });
    renderLearned(await res.json());
  }));
}

function markActive() {
  [...$('tbody').children].forEach((tr, i) => tr.classList.toggle('active', i === activeIndex));
}

function esc(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

function refreshSummary() {
  if (fileMode()) {
    const pages = pickedInfo ? pickedInfo.pages : 1;
    $('summary').textContent = pickedFile
      ? [t('sum.file', pickedFile.name, pages, fileCopies(), pages * fileCopies()), fileFitText()].join('\n')
      : t('sum.nofile');
    $('print').disabled = !pickedFile;
    $('dry').disabled = !pickedFile;
    return;
  }
  $('dry').disabled = false;
  const sel = rows.filter((r) => r.selected && r.sku.trim());
  const total = sel.reduce((a, r) => a + (parseInt(r.qty, 10) || 0), 0);
  $('summary').textContent = t('sum.count', sel.length, total);
  $('print').disabled = sel.length === 0;
}

// --------------------------------------------------------------------------- //
// 预览
// --------------------------------------------------------------------------- //

function refreshPreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(doPreview, 180);
}

// 打印头的物理宽度。纸比它宽的部分永远印不上，跟版式参数无关
function overWideWarning() {
  const dpi = printerRes.dpi_x, dots = printerRes.horz_res;
  if (!dpi || !dots) return '';
  const maxMm = dots / dpi * 25.4;
  const w = paperSpec().width_mm;
  return w > maxMm + 0.5 ? t('paper.toowide', w, Math.round(maxMm * 10) / 10) : '';
}

async function doPreview() {
  // 「打印文件」页签：预览换成那份文件的第一页，底下写它怎么落到纸上
  if (fileMode()) {
    if (pickedInfo && pickedInfo.preview) $('preview').src = pickedInfo.preview;
    else $('preview').removeAttribute('src');
    $('binfo').textContent = [overWideWarning(), fileFitText()].filter(Boolean).join('\n');
    refreshSummary();  // 纸张变了，汇总里那句也要跟着变
    return;
  }
  const r = rows[activeIndex];
  const sku = (r && r.sku ? r.sku : 'EU-PRO-01').trim();
  if (!sku) return;
  const body = JSON.stringify({ sku, paper: paperSpec(), layout: layout() });
  const opts = { method: 'POST', headers: { 'Content-Type': 'application/json' }, body };
  try {
    const res = await fetch('/api/preview', opts);
    if (!res.ok) { $('binfo').textContent = t('preview.fail', (await res.json()).error); return; }
    $('preview').src = URL.createObjectURL(await res.blob());
    const wide = overWideWarning();
    const info = await (await fetch('/api/barcode-info', opts)).json();
    if (info.ok && info.text_only) {
      $('binfo').textContent = [wide, t('bar.textonly')].filter(Boolean).join('\n');
    } else if (info.ok) {
      let msg = t('bar.info', info.modules, info.module_px, info.barcode_width_mm, info.actual_margin_mm);
      if (wide) msg = wide + '\n' + msg;
      if (info.fractional) msg += '\n' + t('bar.fractional', info.module_mil, Math.floor(info.module_px), Math.ceil(info.module_px));
      if (info.fill_rejected) msg += '\n' + t('bar.fillreject');
      if (!info.quiet_zone_ok) msg += '\n' + t('bar.quiet');
      if (info.narrow) msg += '\n' + t('bar.narrow');
      $('binfo').textContent = msg;
    }
  } catch (e) {
    $('binfo').textContent = String(e);
  }
}

// --------------------------------------------------------------------------- //
// 打印
// --------------------------------------------------------------------------- //

async function submitPrint(dryRun) {
  // description 不进标签，只给「历史」页签记一笔，搜的时候能按描述搜
  const sel = rows.filter((r) => r.selected && r.sku.trim())
    .map((r) => ({
      sku: r.sku.trim(), qty: Math.max(1, parseInt(r.qty, 10) || 1),
      description: (r.description || '').trim(),
    }));
  if (!sel.length) return;

  const p = paperSpec();
  const total = sel.reduce((a, r) => a + r.qty, 0);
  if (!dryRun) {
    const list = sel.map((r) => `  ${r.sku}  ×${r.qty}`).join('\n');
    if (!confirm(t('confirm', sel.length, total, $('printer').value, p.width_mm, p.height_mm, list))) return;
  }

  const btn = dryRun ? $('dry') : $('print');
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = t(dryRun ? 'btn.drying' : 'btn.printing');
  const el = $('printResult');
  try {
    const res = await fetch('/api/print', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        printer: $('printer').value, rows: sel, paper: p, layout: layout(), dry_run: dryRun,
      }),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || 'Failed');
    el.className = 'result ok';
    el.textContent = dryRun
      ? t('dry.done', data.files.length, data.total_labels, data.files.join('\n'))
      : t('print.done', data.printed, $('printer').value);
    if (!dryRun && (data.added_to_db || []).length) {
      // 库里没有的 SKU 服务端已自动收进库。库页签正开着就当场重拉，否则下次切过去时再拉
      el.textContent += '\n' + t('db.autoadded', data.added_to_db.length, data.added_to_db.join(', '));
      dbItems = null;
      if (!$('paneDb').classList.contains('hidden')) loadDb();
    }
  } catch (e) {
    el.className = 'result err';
    el.textContent = String(e.message || e);
  } finally {
    btn.disabled = false;
    btn.textContent = old;
    refreshSummary();
  }
}

// 回退一张纸：装纸后机器会多走一张空白去找间隙，收回来下一张就印在它上面。
// 不出纸，所以不弹确认框。
async function submitBackfeed() {
  const btn = $('backfeed');
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = t('btn.backfeeding');
  const el = $('printResult');
  try {
    const res = await fetch('/api/backfeed', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ printer: $('printer').value, paper: paperSpec() }),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || 'Failed');
    el.className = 'result ok';
    el.textContent = t('backfeed.done', data.mm);
  } catch (e) {
    el.className = 'result err';
    el.textContent = String(e.message || e);
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
}

// --------------------------------------------------------------------------- //
// 事件绑定
// --------------------------------------------------------------------------- //

function syncRangeLabels() {
  $('vmargin').textContent = $('marginx').value + 'mm';
  $('vtop').textContent = $('btop').value + '%';
  $('vbh').textContent = $('bheight').value + '%';
  $('vth').textContent = $('theight').value + '%';
  const fill = parseFloat($('textfill').value);
  $('vfill').textContent = fill > 0 ? fill + '%' : t('set.auto');
}

function bind() {
  const drop = $('drop');
  drop.addEventListener('click', () => $('file').click());
  $('file').addEventListener('change', (e) => { if (e.target.files[0]) handleImage(e.target.files[0]); });

  ['dragenter', 'dragover'].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', (e) => {
    const f = e.dataTransfer.files[0];
    if (f && f.type.startsWith('image/')) handleImage(f);
  });

  $('lang').addEventListener('click', switchLang);
  document.querySelectorAll('.tab').forEach((el) =>
    el.addEventListener('click', () => switchTab(el.dataset.tab)));
  $('parseText').addEventListener('click', parseText);
  // 在文本框里 Ctrl+Enter 直接解析
  $('text').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); parseText(); }
  });

  // Excel 截图直接 Ctrl+V（在文本框里粘贴文字时不拦截）
  document.addEventListener('paste', (e) => {
    if (e.target === $('text')) return;
    for (const item of e.clipboardData.items) {
      if (item.type.startsWith('image/')) { handleImage(item.getAsFile()); e.preventDefault(); return; }
    }
  });

  $('reupload').addEventListener('click', () => {
    $('shot').classList.add('hidden');
    $('drop').classList.remove('hidden');
    setStatus('');
  });

  // 截图点一下放大到原始尺寸，方便和右边列表逐行对
  $('shotBox').addEventListener('click', () => $('shotBox').classList.toggle('zoom'));

  $('printer').addEventListener('change', (e) => loadPapers(e.target.value, null));
  $('paper').addEventListener('change', onPaperChange);
  $('textonly').addEventListener('change', () => { syncTextOnly(); refreshPreview(); });
  ['wmm', 'hmm', 'rotate', 'modulepx', 'showtext'].forEach((id) =>
    $(id).addEventListener('change', refreshPreview));
  ['marginx', 'btop', 'bheight', 'theight', 'textfill'].forEach((id) =>
    $(id).addEventListener('input', () => { syncRangeLabels(); refreshPreview(); }));

  $('chkAll').addEventListener('change', (e) => {
    rows.forEach((r) => { r.selected = e.target.checked; });
    renderTable();
  });
  // 展开时才拉，别在页面加载时多打一次接口
  $('learnedWrap').addEventListener('toggle', (e) => { if (e.target.open) loadLearned(); });
  $('addRow').addEventListener('click', () => {
    rows.push({
      selected: true, description: '', sku: '', qty: 1, low_confidence: false, qty_detected: true,
      source: 'manual', origin: '', junk: false, corrected_from: null, suggestion: null,
    });
    activeIndex = rows.length - 1;
    renderTable();
  });

  // SKU 库页签
  $('dbQ').addEventListener('input', renderDb);
  $('dbQ').addEventListener('keydown', (e) => {
    // 回车 = 勾上唯一的那条搜索结果。只是勾选，加不加进列表还要点按钮
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const boxes = $('dbList').querySelectorAll('input[type=checkbox]');
    if (boxes.length === 1 && !boxes[0].checked) boxes[0].click();
  });
  $('dbPick').addEventListener('click', pickDbToList);
  $('dbClear').addEventListener('click', () => { dbPicked.clear(); renderDb(); });
  $('dbQuickAdd').addEventListener('click', quickAddDb);
  $('dbImport').addEventListener('click', importDb);

  $('print').addEventListener('click', () => (fileMode() ? submitFilePrint(false) : submitPrint(false)));
  $('dry').addEventListener('click', () => (fileMode() ? submitFilePrint(true) : submitPrint(true)));
  $('backfeed').addEventListener('click', submitBackfeed);

  // 打印文件页签
  const dropF = $('dropFile');
  dropF.addEventListener('click', () => $('fileDoc').click());
  $('fileDoc').addEventListener('change', (e) => { if (e.target.files[0]) handleDocFile(e.target.files[0]); });
  ['dragenter', 'dragover'].forEach((ev) =>
    dropF.addEventListener(ev, (e) => { e.preventDefault(); dropF.classList.add('over'); }));
  ['dragleave', 'drop'].forEach((ev) =>
    dropF.addEventListener(ev, (e) => { e.preventDefault(); dropF.classList.remove('over'); }));
  dropF.addEventListener('drop', (e) => { if (e.dataTransfer.files[0]) handleDocFile(e.dataTransfer.files[0]); });
  $('fileReupload').addEventListener('click', () => {
    pickedFile = null; pickedInfo = null;
    $('fileReady').classList.add('hidden');
    $('dropFile').classList.remove('hidden');
    refreshSummary();
    refreshPreview();
  });
  // 换纸、换打印机（标签机 ↔ A4）都会走 onPaperChange → refreshPreview，文件模式下那里会重算汇总
  $('fileCopies').addEventListener('input', refreshSummary);
}

init();
