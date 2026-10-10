/* RetailIQ dashboard - vanilla JS. Endpoints: /api/* (FastAPI). */
const $ = (s, r = document) => r.querySelector(s);
const api = (p, o) => fetch('/api' + p, o).then(r => { if (!r.ok) throw new Error(p + ' ' + r.status); return r.json(); });
const post = (p, b) => api(p, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b || {}) });
const state = { store: '', kind: '', charts: {}, stores: [] };
const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const usd = (x) => 'USD ' + Math.round(x).toLocaleString('es-MX');
const num = (x, d = 0) => Number(x).toLocaleString('es-MX', { maximumFractionDigits: d });
const q = (k = 'store_id') => state.store ? `${k}=${state.store}` : '';
const ICON = {
  inventory: '<path d="M3 7.5 12 3l9 4.5v9L12 21l-9-4.5z"/><path d="M3 7.5 12 12l9-4.5M12 12v9"/>',
  promotion: '<path d="M20 12 12 20 4 12V4h8z"/><circle cx="8.5" cy="8.5" r="1.2"/>',
  staffing: '<circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5M16 5.2a3.2 3.2 0 0 1 0 5.6M18 14.8c1.8.7 3 2.4 3 5.2"/>'
};

function toast(t) { const e = $('#toast'); e.textContent = t; e.classList.add('on'); clearTimeout(toast.t); toast.t = setTimeout(() => e.classList.remove('on'), 2600); }
function swap(el) { el.classList.remove('fade-swap'); void el.offsetWidth; el.classList.add('fade-swap'); }

/* pointer light on glass surfaces */
document.addEventListener('pointermove', (e) => {
  const g = e.target.closest?.('.glass'); if (!g) return;
  const r = g.getBoundingClientRect();
  g.style.setProperty('--mx', (e.clientX - r.left) + 'px'); g.style.setProperty('--my', (e.clientY - r.top) + 'px');
}, { passive: true });

function countUp(el, to, fmt) {
  if (reduce || !isFinite(to)) { el.textContent = fmt(to); return; }
  const t0 = performance.now(), dur = 900;
  const step = (t) => { const p = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - p, 3); el.textContent = fmt(to * e); if (p < 1) requestAnimationFrame(step); };
  requestAnimationFrame(step);
}

/* ------------------------------------------------ init */
async function waitReady() {
  let box = null;
  for (;;) {
    const h = await fetch('/api/health').then(r => r.json());
    if (h.ready) { box?.remove(); return; }
    if (h.error) throw new Error(h.error);
    if (!box) { box = document.createElement('div'); box.className = 'loading'; document.body.appendChild(box); }
    box.innerHTML = `<div class="glass card"><div class="spin"></div><h2>Preparando tu panel</h2><p class="hint">${esc(h.stage)}…<br>La primera vez tarda unos minutos.</p></div>`;
    await new Promise(r => setTimeout(r, 2500));
  }
}

async function init() {
  await waitReady();
  const [cfg, stores] = await Promise.all([api('/config'), api('/stores')]);
  state.stores = stores; state.cfg = cfg;
  const bySite = {};
  stores.forEach(s => (bySite[s.site_name] ||= []).push(s));
  $('#store').innerHTML = '<option value="">Toda la red · ' + stores.filter(s => s.active).length + ' tiendas activas</option>' +
    Object.entries(bySite).map(([site, arr]) => `<optgroup label="${esc(site)}">` +
      arr.map(s => `<option value="${s.store_id}">${esc(s.store_id)} · ${esc(s.store_name)}${s.active ? '' : ' (cerrada)'}</option>`).join('') + '</optgroup>').join('');
  $('#store').onchange = (e) => { state.store = e.target.value; refresh(true); };
  $('#seg').onclick = (e) => { const b = e.target.closest('button'); if (!b) return; state.kind = b.dataset.k; moveThumb(b); loadRecs(); };
  $('#rerun').onclick = async (e) => {
    e.target.disabled = true; e.target.textContent = 'Calculando…';
    try { await post('/engine/run'); toast('Modelos y recomendaciones actualizados'); await refresh(true); }
    catch (err) { toast('No se pudo recalcular'); } finally { e.target.disabled = false; e.target.textContent = 'Recalcular'; }
  };
  $('#askForm').onsubmit = async (e) => {
    e.preventDefault(); const t = $('#question').value.trim(); if (!t) return;
    $('#answer').textContent = 'Pensando…';
    const r = await post('/ai/ask', { store_id: state.store || null, question: t }); $('#answer').textContent = r.answer; swap($('#answer'));
  };
  renderPbi(cfg);
  moveThumb($('#seg .on'));
  addEventListener('resize', () => moveThumb($('#seg .on')));
  refresh(false);
  loadStatic();
}
function moveThumb(btn) {
  [...$('#seg').querySelectorAll('button')].forEach(b => b.classList.toggle('on', b === btn));
  const t = $('#seg .thumb'); t.style.width = btn.offsetWidth + 'px'; t.style.transform = `translateX(${btn.offsetLeft - 4}px)`;
}
function refresh(animate) {
  loadKpis(animate); loadTrend(); loadRecs(); loadStaffing(); loadInventory(); loadBrief(); loadModel();
}
function loadStatic() { loadStores(); loadPromos(); loadSuppliers(); loadDq(); }

/* ------------------------------------------------ KPIs */
async function loadKpis(animate) {
  const k = await api('/kpis?' + q());
  $('#asof').textContent = 'Datos al ' + new Date(k.as_of + 'T12:00').toLocaleDateString('es-MX', { day: 'numeric', month: 'short', year: 'numeric' }) + ' · pronóstico 14 días';
  const ch = k.change_pct;
  const tiles = [
    { l: 'Ventas netas · 30 días', v: k.net_sales_30d, f: usd, d: `<span class="pill ${ch >= 0 ? '' : 'neg'}">${ch >= 0 ? '▲' : '▼'} ${Math.abs(ch)}%</span> vs. 30 días previos` },
    { l: 'Pronóstico · 14 días', v: k.forecast_14d, f: usd, d: `<span class="pill neutral">modelo</span> incluye feriados y clima típico` },
    { l: 'Merma · 30 días', v: k.waste_usd_30d, f: usd, d: 'producto caducado, a costo' },
    { l: 'Horas en quiebre · 30 días', v: k.stockout_hours_30d, f: (x) => num(x), d: 'SKU-horas sin disponibilidad' },
    { l: 'Personal · 14 días', v: k.short_hours, f: (x) => '+' + num(x) + ' h', d: `<span class="pill neutral">−${num(k.over_hours)} h</span> por reducir` },
  ];
  const root = $('#kpis');
  if (!root.children.length || !animate) {
    root.innerHTML = tiles.map((t, i) => `<div class="kpi glass" style="--d:${i * .06}s"><div class="l">${t.l}</div><div class="v" data-i="${i}">0</div><div class="d">${t.d}</div></div>`).join('');
  } else {
    [...root.children].forEach((c, i) => { $('.d', c).innerHTML = tiles[i].d; swap(c); });
  }
  [...root.children].forEach((c, i) => countUp($('.v', c), tiles[i].v, tiles[i].f));
}

async function loadModel() {
  const m = await api('/model');
  if (m.metrics) $('#modelinfo').textContent = `Error del modelo (WAPE) ${(m.metrics.store.wape_model * 100).toFixed(1)}% · ${m.metrics.store.improvement_vs_naive_pct}% mejor que el promedio simple`;
}

/* ------------------------------------------------ trend chart */
async function loadTrend() {
  const t = await api('/sales/trend?' + q());
  const a = t.actual, f = t.forecast, labels = [...a.map(x => x.date), ...f.map(x => x.date)];
  const pad = (n) => Array(n).fill(null);
  const act = [...a.map(x => x.v), ...pad(f.length)];
  const mid = [...pad(a.length - 1), a[a.length - 1].v, ...f.map(x => x.v)];
  const lo = [...pad(a.length - 1), a[a.length - 1].v, ...f.map(x => x.lo)];
  const hi = [...pad(a.length - 1), a[a.length - 1].v, ...f.map(x => x.hi)];
  const ink = css('--ink3'), grid = 'rgba(120,145,180,.16)';
  state.charts.trend?.destroy();
  const ctx = $('#trend').getContext('2d');
  const fillG = ctx.createLinearGradient(0, 0, 0, 300); fillG.addColorStop(0, 'rgba(47,109,246,.20)'); fillG.addColorStop(1, 'rgba(47,109,246,0)');
  state.charts.trend = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets: [
      { label: 'Real', data: act, borderColor: css('--slate'), backgroundColor: 'rgba(45,66,99,.06)', fill: true, borderWidth: 2, pointRadius: 0, pointHoverRadius: 5, tension: .3 },
      { label: 'Pronóstico', data: mid, borderColor: css('--accent'), borderDash: [6, 5], borderWidth: 2.2, pointRadius: 0, pointHoverRadius: 5, tension: .3 },
      { label: 'Banda 80 %', data: hi, borderWidth: 0, pointRadius: 0, backgroundColor: fillG, fill: '+1', tension: .3 },
      { label: 'inf', data: lo, borderWidth: 0, pointRadius: 0, tension: .3 }] },
    options: { responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
      animation: reduce ? false : { duration: 1100, easing: 'easeOutQuart' },
      plugins: { legend: { position: 'bottom', labels: { color: ink, usePointStyle: true, boxWidth: 8, filter: (i) => i.text !== 'inf' } },
        tooltip: { backgroundColor: 'rgba(14,26,43,.9)', padding: 10, cornerRadius: 10, filter: (c) => c.dataset.label !== 'inf' && c.dataset.label !== 'Banda 80 %',
          callbacks: { label: (c) => `${c.dataset.label}: ${usd(c.parsed.y)}` } } },
      scales: { x: { ticks: { color: ink, maxTicksLimit: 8, callback(v) { const d = this.getLabelForValue(v); return new Date(d + 'T12:00').toLocaleDateString('es-MX', { day: 'numeric', month: 'short' }); } }, grid: { display: false } },
        y: { beginAtZero: true, ticks: { color: ink, callback: (v) => (v >= 1000 ? (v / 1000).toLocaleString('es-MX') + ' k' : v) }, grid: { color: grid }, border: { display: false } } } } });
  swap($('.chart'));
  $('#trendTable').innerHTML = '<thead><tr><th>Fecha</th><th>Ventas netas</th><th>Tipo</th></tr></thead><tbody>' +
    [...a.slice(-10).map(x => [x.date, x.v, 'real']), ...f.map(x => [x.date, x.v, 'pronóstico'])].map(r => `<tr><td>${r[0]}</td><td>${usd(r[1])}</td><td>${r[2]}</td></tr>`).join('') + '</tbody>';
}

/* ------------------------------------------------ AI */
async function loadBrief() {
  $('#brief').innerHTML = '<div class="skeleton"></div>'; $('#answer').textContent = '';
  const b = await post('/ai/brief', { store_id: state.store || null });
  const live = b.source.startsWith('gemini');
  $('#aisource').textContent = live ? 'Gemini' : 'Resumen sin conexión'; $('#aisource').title = b.source;
  const list = (t, a) => a && a.length ? `<div class="sub">${t}</div><ul>${a.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : '';
  $('#brief').innerHTML = `<h3>${esc(b.headline)}</h3>${list('Puntos clave', b.key_points)}${list('Hacer primero', b.top_actions)}${list('Riesgos', b.risks)}`;
  swap($('#brief'));
}

/* ------------------------------------------------ recommendations */
async function loadRecs() {
  const kinds = state.kind ? [state.kind] : ['inventory', 'promotion', 'staffing'];
  const per = state.kind ? 24 : 8;
  const [lists, k] = await Promise.all([Promise.all(kinds.map(kd => api(`/recommendations?${q()}&kind=${kd}&limit=${per}`))), api('/kpis?' + q())]);
  const r = lists.flat().sort((a, b) => a.priority - b.priority || b.est_impact - a.est_impact);
  $('#recsum').textContent = `${num(k.open_recommendations)} abiertas · impacto potencial ${usd(k.potential_impact)}`;
  const el = $('#recs');
  el.innerHTML = r.length ? r.map((x, i) => card(x, i)).join('') : '<p class="hint">No hay recomendaciones abiertas para este filtro.</p>';
}
function card(x, i) {
  const p = x.payload || {};
  const extra = x.kind === 'staffing' ? `<div class="meta">${(p.days || []).slice(0, 4).map(d => `<span class="chip">${new Date(d.date + 'T12:00').toLocaleDateString('es-MX', { weekday: 'short', day: 'numeric' })} · ${d.hours} h</span>`).join('')}</div>` : '';
  const sign = x.kind === 'promotion' ? `<button class="btn sm" onclick="signCopy(${x.id})">Texto de cartel (Gemini)</button>` : '';
  return `<div class="rec" id="rec${x.id}" style="--i:${i}">
    <div class="rec-h"><span class="ico ${x.kind}"><svg viewBox="0 0 24 24">${ICON[x.kind]}</svg></span>
      <div><div class="t">${esc(x.title)}</div><div class="meta"><span class="dot p${x.priority}" title="Prioridad ${x.priority}"></span>${esc(x.store)}</div></div></div>
    <div class="s">${esc(x.summary)}</div>${extra}
    <div class="meta"><span class="impact" title="Impacto estimado">+${usd(x.est_impact)}</span></div>
    <div class="acts"><button class="btn solid sm" onclick="act(${x.id},'approve')">Aprobar</button><button class="btn sm" onclick="act(${x.id},'dismiss')">Descartar</button>${sign}</div>
    <div id="copy${x.id}"></div></div>`;
}
async function act(id, a) {
  const el = $('#rec' + id); el.classList.add('leaving');
  await post(`/recommendations/${id}/${a}`);
  setTimeout(() => el.remove(), 330); toast(a === 'approve' ? 'Acción aprobada' : 'Recomendación descartada'); loadKpis(false);
}
async function signCopy(id) {
  const r = await post('/ai/promo-copy/' + id);
  $('#copy' + id).innerHTML = `<div class="copy">ES: ${esc(r.es)}<br>EN: ${esc(r.en)}${r.social ? '<br>Social: ' + esc(r.social) : ''}<br><span class="hint">${esc(r.source)}</span></div>`;
}
window.act = act; window.signCopy = signCopy;

/* ------------------------------------------------ staffing */
async function loadStaffing() {
  const rows = await api('/staffing?' + q());
  const dates = [...new Set(rows.map(r => r.date))];
  const box = $('#staff');
  if (state.store) {
    $('#staffTitle').textContent = 'Horas de personal: programadas vs. necesarias';
    $('#staffHint').textContent = 'según ventas pronosticadas';
    box.innerHTML = '<div class="chart short"><canvas id="staffChart"></canvas></div><div class="legend"><span><i class="sw ok"></i>habitual (4 semanas)</span><span><i class="sw over"></i>necesarias</span></div>';
    state.charts.staff?.destroy();
    if (!rows.length) { box.innerHTML = '<p class="hint">Sin plan para esta tienda (cerrada o sin datos).</p>'; return; }
    state.charts.staff = new Chart($('#staffChart'), {
      type: 'bar',
      data: { labels: dates.map(d => new Date(d + 'T12:00').toLocaleDateString('es-MX', { weekday: 'short', day: 'numeric' })),
        datasets: [{ label: 'Habitual', data: rows.map(r => r.baseline), backgroundColor: 'rgba(160,178,205,.55)', borderRadius: 6 },
                   { label: 'Necesarias', data: rows.map(r => r.required), backgroundColor: css('--over'), borderRadius: 6 }] },
      options: { responsive: true, maintainAspectRatio: false, animation: reduce ? false : { duration: 900, easing: 'easeOutQuart' },
        plugins: { legend: { display: false }, tooltip: { backgroundColor: 'rgba(14,26,43,.9)', cornerRadius: 10, callbacks: { label: c => `${c.dataset.label}: ${c.parsed.y} h` } } },
        scales: { x: { grid: { display: false }, ticks: { color: css('--ink3'), maxRotation: 0, font: { size: 10 } } }, y: { beginAtZero: true, grid: { color: 'rgba(120,145,180,.16)' }, border: { display: false }, ticks: { color: css('--ink3') } } } } });
    return;
  }
  $('#staffTitle').textContent = 'Personal: mayores desbalances · 14 días';
  $('#staffHint').textContent = 'horas necesarias − habituales';
  const by = {}; rows.forEach(r => (by[r.store_id] ||= []).push(r));
  const names = Object.fromEntries(state.stores.map(s => [s.store_id, s.store_name]));
  const top = Object.entries(by).map(([id, a]) => [id, a, a.reduce((s, r) => s + Math.abs(r.delta), 0)]).sort((x, y) => y[2] - x[2]).slice(0, 12);
  let h = `<div class="heat"><div class="row2"><span></span>${dates.map(d => `<span class="hd">${new Date(d + 'T12:00').getDate()}</span>`).join('')}</div>`;
  top.forEach(([id, a], ri) => {
    h += `<div class="row2"><span class="lab" title="${esc(names[id])}">${id} · ${esc((names[id] || '').replace(/^.*[–-]\s*/, ''))}</span>` + a.map((r, ci) => {
      const cls = r.status === 'short' ? 'short' : r.status === 'over' ? 'over' : '';
      const a_ = Math.min(3, Math.round(Math.abs(r.delta) / 6));
      return `<span class="c ${cls}" style="--a:${a_};--j:${ri + ci}" title="${esc(names[id])} · ${r.date}: necesarias ${r.required} h, habituales ${r.baseline} h">${cls ? (r.delta > 0 ? '+' : '') + Math.round(r.delta) : ''}</span>`;
    }).join('') + '</div>';
  });
  box.innerHTML = h + '</div><div class="legend"><span><i class="sw short"></i>faltan horas</span><span><i class="sw ok"></i>equilibrado (±10 %)</span><span><i class="sw over"></i>sobran horas</span><span>Fechas: octubre 2026</span></div>';
}

/* ------------------------------------------------ inventory lists */
async function loadInventory() {
  const r = await api(`/recommendations?${q()}&kind=inventory&limit=1500`);
  const re = r.filter(x => x.payload.action === 'reorder').sort((a, b) => b.est_impact - a.est_impact).slice(0, 7);
  const rd = r.filter(x => x.payload.action === 'reduce_order').sort((a, b) => b.est_impact - a.est_impact).slice(0, 7);
  const row = (n, sub, pct, val, color) => `<div class="bar-r"><div class="n">${esc(n)}<small>${esc(sub)}</small></div><div class="track"><div class="fill" data-w="${pct}" style="background:${color}"></div></div><div class="v">${val}</div></div>`;
  $('#inv').innerHTML = `<div class="sec">Quiebre probable · margen en riesgo</div>` +
    (re.length ? re.map(x => row(x.payload.product, `${x.store} · pedir ${x.payload.order_qty} · cobertura ${num(x.payload.cover_days, 1)} d`, Math.min(100, x.est_impact / (re[0].est_impact || 1) * 100), usd(x.est_impact).replace('USD ', '$'), css('--short'))).join('') : '<p class="hint">Sin riesgos de quiebre.</p>') +
    `<div class="sec">Merma probable · USD en riesgo</div>` +
    (rd.length ? rd.map(x => row(x.payload.product, `${x.store} · vida útil ${x.payload.shelf_life_days} d`, Math.min(100, x.est_impact / (rd[0].est_impact || 1) * 100), usd(x.est_impact).replace('USD ', '$'), css('--over'))).join('') : '<p class="hint">Sin riesgos de merma.</p>');
  requestAnimationFrame(() => $('#inv').querySelectorAll('.fill').forEach(f => f.style.width = f.dataset.w + '%'));
  swap($('#inv'));
}

/* ------------------------------------------------ static panels */
async function loadStores() {
  const s = await api('/stores');
  $('#stores').innerHTML = '<thead><tr><th>Tienda</th><th>Sitio</th><th>Formato</th><th>Ventas 30 d</th><th>Δ</th><th>Merma 30 d</th><th>Horas en quiebre</th><th>Acciones</th><th>Impacto</th></tr></thead><tbody>' +
    s.filter(x => x.active).sort((a, b) => b.rev - a.rev).map(x => `<tr><td>${x.store_id} · ${esc(x.store_name)}</td><td>${esc(x.site_name)}</td><td>${esc(x.format)}</td><td>${usd(x.rev)}</td><td class="${x.change_pct >= 0 ? 'up' : 'down'}">${x.change_pct}%</td><td>${usd(x.waste)}</td><td>${num(x.so_h)}</td><td>${x.n}</td><td>${usd(x.impact)}</td></tr>`).join('') + '</tbody>';
}
async function loadPromos() {
  const p = (await api('/promotions/performance')).filter(x => x.expected_uplift_pct != null);
  state.charts.promo?.destroy();
  state.charts.promo = new Chart($('#promoChart'), {
    type: 'bar',
    data: { labels: p.map(x => x.promo_name), datasets: [
      { label: 'Esperado', data: p.map(x => x.expected_uplift_pct), backgroundColor: 'rgba(160,178,205,.6)', borderRadius: 5 },
      { label: 'Real', data: p.map(x => x.realized_uplift_pct), backgroundColor: css('--accent'), borderRadius: 5 }] },
    options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, animation: reduce ? false : { duration: 1000, easing: 'easeOutQuart' },
      plugins: { legend: { position: 'bottom', labels: { usePointStyle: true, boxWidth: 8, color: css('--ink3') } }, tooltip: { backgroundColor: 'rgba(14,26,43,.9)', cornerRadius: 10, callbacks: { label: c => `${c.dataset.label}: ${c.parsed.x == null ? 'n/d' : num(c.parsed.x, 1) + ' %'}` } } },
      scales: { x: { grid: { color: 'rgba(120,145,180,.16)' }, border: { display: false }, ticks: { color: css('--ink3'), callback: v => v + ' %' } }, y: { grid: { display: false }, ticks: { color: css('--ink2'), font: { size: 11 } } } } } });
  $('#promoNotes').innerHTML = p.filter(x => x.note).map(x => `<span>• ${esc(x.promo_name)}: ${esc(x.note)}</span>`).join('') +
    '<span>• El incremento real compara las 4 semanas previas con el periodo de la promo; es indicativo (no controla estacionalidad).</span>';
}
async function loadSuppliers() {
  const s = await api('/suppliers/performance');
  const dual = (c, a, lab) => `<div class="dual"><span>${lab}: ${num(a * 100, 1)} % <small>(contrato ${num(c * 100, 0)} %)</small></span><div class="track"><div class="fill c" style="width:${c * 100}%"></div></div><div class="track"><div class="fill a" style="width:${a * 100}%"></div></div></div>`;
  $('#sup').innerHTML = s.map(x => `<div class="sup-r"><div>${esc(x.supplier_name)}<small>${num(x.po_lines)} líneas</small></div>${dual(x.contract_fill_rate, x.fill_rate, 'Surtido')}${x.on_time_rate == null ? '<span class="hint">sin fechas</span>' : dual(x.contract_on_time_rate, x.on_time_rate, 'A tiempo')}</div>`).join('');
}
async function loadDq() {
  const d = await api('/data-quality');
  $('#dq').innerHTML = '<thead><tr><th>Tabla</th><th>Problema</th><th>Filas</th><th>Tratamiento</th></tr></thead><tbody>' +
    d.map(x => `<tr><td>${esc(x.table)}</td><td class="wrapc">${esc(x.issue)}</td><td>${num(x.rows)}</td><td class="wrapc">${esc(x.treatment)}</td></tr>`).join('') + '</tbody>';
}
function renderPbi(cfg) {
  const base = location.origin + '/api/export';
  $('#pbiBody').innerHTML = (cfg.powerbi_embed_url ? `<iframe src="${esc(cfg.powerbi_embed_url)}" allowfullscreen title="Reporte Power BI"></iframe>` :
    '<p>Aún no hay un reporte incrustado. Define <code>POWERBI_EMBED_URL</code> para mostrarlo aquí.</p>') +
    `<p>En Power BI Desktop: <b>Obtener datos → Web</b> → <code>${base}/recommendations.csv</code>${cfg.export_auth ? ' (encabezado <code>X-API-Key</code>)' : ''}, o conéctate al esquema <code>rpt</code> de PostgreSQL para los hechos grandes. Guía en <code>powerbi/README.md</code>.</p>`;
}

init().catch(e => { document.querySelector('main').innerHTML = `<div class="glass card">No se pudo conectar con la API (${esc(e.message)}). ¿Está corriendo el backend?</div>`; });
