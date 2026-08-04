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
const QUICK_SIZES = [[60, 30], [90, 50], [100, 150]];

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
      selected: true,
      description: r.description || '',
      sku: r.sku,
      qty: r.qty,
      low_confidence: r.low_confidence,
      qty_detected: r.qty_detected,
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

function switchTab(name) {
  document.querySelectorAll('.tab').forEach((el) => el.classList.toggle('on', el.dataset.tab === name));
  $('paneImg').classList.toggle('hidden', name !== 'img');
  $('paneTxt').classList.toggle('hidden', name !== 'txt');
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
    if (i === activeIndex) tr.classList.add('active');

    tr.innerHTML = `
      <td><input type="checkbox" ${r.selected ? 'checked' : ''} data-k="selected"></td>
      <td><input type="text" value="${esc(r.description)}" data-k="description"></td>
      <td><input type="text" class="sku" value="${esc(r.sku)}" data-k="sku"></td>
      <td><input type="number" min="1" max="999" value="${r.qty}" data-k="qty"></td>
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
      inp.addEventListener('focus', () => { activeIndex = i; markActive(); refreshPreview(); });
    });
    tr.querySelector('.del').addEventListener('click', () => {
      rows.splice(i, 1);
      if (activeIndex >= rows.length) activeIndex = Math.max(0, rows.length - 1);
      renderTable();
    });
    tb.appendChild(tr);
  });
  $('empty').classList.toggle('hidden', rows.length > 0);
  refreshSummary();
  refreshPreview();
}

function markActive() {
  [...$('tbody').children].forEach((tr, i) => tr.classList.toggle('active', i === activeIndex));
}

function esc(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

function refreshSummary() {
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
  const sel = rows.filter((r) => r.selected && r.sku.trim())
    .map((r) => ({ sku: r.sku.trim(), qty: Math.max(1, parseInt(r.qty, 10) || 1) }));
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
  $('addRow').addEventListener('click', () => {
    rows.push({ selected: true, description: '', sku: '', qty: 1, low_confidence: false, qty_detected: true });
    activeIndex = rows.length - 1;
    renderTable();
  });

  $('print').addEventListener('click', () => submitPrint(false));
  $('dry').addEventListener('click', () => submitPrint(true));
}

init();
