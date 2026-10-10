/* RetailIQ dashboard - vanilla JS. Endpoints: /api/* (FastAPI). */
const $ = (s, r = document) => r.querySelector(s);
const adminKey = () => { try { return sessionStorage.getItem('adminKey') || ''; } catch (e) { return ''; } };
async function api(p, o = {}) {
  const go = () => fetch('/api' + p, { ...o, headers: { ...(o.headers || {}), 'X-Admin-Key': adminKey() } });
  let r = await go();
  if (r.status === 401) {                       // write action on a protected deployment: ask for the key once
    const k = prompt('Esta acción requiere la clave de administrador:');
    if (k) { try { sessionStorage.setItem('adminKey', k); } catch (e) { /* ignore */ } r = await go(); }
  }
  if (!r.ok) throw new Error(p + ' ' + r.status);
  return r.json();
}
const post = (p, b) => api(p, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b || {}) });
const state = { store: '', kind: '', charts: {}, stores: [], planDate: null, plan: null, net: null };
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
  addEventListener('resize', () => { moveThumb($('#seg .on')); moveDayThumb(); });
  refresh(false);
  loadStatic();
}
function moveThumb(btn) {
  [...$('#seg').querySelectorAll('button')].forEach(b => b.classList.toggle('on', b === btn));
  const t = $('#seg .thumb'); t.style.width = btn.offsetWidth + 'px'; t.style.transform = `translateX(${btn.offsetLeft - 4}px)`;
}
function refresh(animate) {
  loadKpis(animate); loadPlan(); loadTrend(); loadRecs(); loadStaffing(); loadInventory(); loadBrief(); loadModel(); loadBacktest();
}
function loadStatic() { loadStores(); loadPromos(); loadSuppliers(); loadDq(); loadAssump(); }


/* ------------------------------------------------ daily plan (per store, per day) */
const money = (x) => '$' + Math.round(x || 0).toLocaleString('es-MX');
const kmoney = (x) => !x ? '' : x >= 1000 ? '$' + (x / 1000).toLocaleString('es-MX', { maximumFractionDigits: 1 }) + ' k' : '$' + Math.round(x);
const dLong = (d) => { const t = new Date(d + 'T12:00').toLocaleDateString('es-MX', { weekday: 'long', day: 'numeric', month: 'long' }); return t[0].toUpperCase() + t.slice(1); };
const dShort = (d) => new Date(d + 'T12:00').toLocaleDateString('es-MX', { weekday: 'short', day: 'numeric', month: 'short' }).replace('.', '');
const dRange = (a, b) => `${new Date(a + 'T12:00').getDate()}–${new Date(b + 'T12:00').toLocaleDateString('es-MX', { day: 'numeric', month: 'short' }).replace('.', '')}`;

async function loadPlan() {
  const body = $('#planBody');
  try {
    let days;
    if (state.store) {
      state.plan = await api('/plan?store_id=' + state.store); state.net = null;
      days = state.plan.days.map(d => ({ date: d.date, n: d.orders.length + d.promos.length, status: d.staff?.status, impact: d.impact }));
      $('#planHint').textContent = `${state.plan.store} · qué reponer, qué promocionar y cuánto personal asignar, día por día`;
    } else {
      state.net = await api('/plan/network'); state.plan = null;
      days = state.net.dates.map(dt => {
        const c = state.net.cells.filter(x => x.date === dt);
        return { date: dt, n: c.reduce((s, x) => s + x.orders + x.promos, 0), status: null, impact: c.reduce((s, x) => s + x.impact, 0) };
      });
      $('#planHint').textContent = 'Toda la red · elige una tienda arriba (o una fila) para ver su plan detallado';
    }
    if (!days.length) { $('#planDays').innerHTML = '<i class="dthumb"></i>'; body.innerHTML = '<p class="hint">Aún no hay plan: pulsa «Recalcular».</p>'; return; }
    if (!days.some(d => d.date === state.planDate)) state.planDate = days[0].date;
    renderDays(days);
    renderPlanBody();
  } catch (e) { body.innerHTML = '<p class="hint">No se pudo cargar el plan del día.</p>'; }
}
function renderDays(days) {
  const box = $('#planDays');
  box.innerHTML = '<i class="dthumb"></i>' + days.map(d => {
    const dt = new Date(d.date + 'T12:00');
    return `<button class="day${d.date === state.planDate ? ' on' : ''}" role="tab" data-d="${d.date}" title="${esc(dLong(d.date))} · ${d.n} acciones">
      <span class="wd">${dt.toLocaleDateString('es-MX', { weekday: 'short' }).replace('.', '')}</span><span class="dn">${dt.getDate()}</span>
      <i class="sd ${d.status || ''}"></i><span class="di">${kmoney(d.impact) || '—'}</span></button>`;
  }).join('');
  box.onclick = (e) => {
    const b = e.target.closest('.day'); if (!b) return;
    state.planDate = b.dataset.d;
    box.querySelectorAll('.day').forEach(x => x.classList.toggle('on', x === b));
    moveDayThumb(); renderPlanBody(); swap($('#planBody'));
  };
  requestAnimationFrame(moveDayThumb);
}
function moveDayThumb() {
  const on = $('#planDays .day.on'), t = $('#planDays .dthumb'); if (!on || !t) return;
  t.style.width = on.offsetWidth + 'px'; t.style.transform = `translateX(${on.offsetLeft}px)`;
  on.scrollIntoView({ block: 'nearest', inline: 'center', behavior: reduce ? 'auto' : 'smooth' });
}
function renderPlanBody() { state.store ? renderStorePlan() : renderNetworkPlan(); }

function renderNetworkPlan() {
  const cells = state.net.cells.filter(c => c.date === state.planDate).sort((a, b) => b.impact - a.impact);
  const tot = cells.reduce((s, c) => s + c.impact, 0), acts = cells.reduce((s, c) => s + c.orders + c.promos, 0);
  const chipS = (c) => c.status === 'short' ? `<span class="pill neg">+${num(c.delta, 0)} h</span>` : c.status === 'over' ? `<span class="pill neutral">${num(c.delta, 0)} h</span>` : '<span class="pill">equilibrado</span>';
  $('#planBody').innerHTML = `<div class="phead"><h3>${esc(dLong(state.planDate))}</h3>
      <div class="pstats"><span class="chip">${num(acts)} acciones en ${cells.length} tiendas</span><span class="impact">+${money(tot)} potencial</span></div></div>
    <div class="tbl free"><table><thead><tr><th>Tienda</th><th>Pedidos</th><th>Promociones</th><th>Personal</th><th>Impacto</th></tr></thead><tbody>` +
    cells.slice(0, 15).map(c => `<tr class="click" data-s="${c.store_id}"><td>${esc(c.store_id)} · ${esc(c.store)}</td><td>${c.orders}</td><td>${c.promos}</td><td>${chipS(c)}</td><td>${money(c.impact)}</td></tr>`).join('') +
    `</tbody></table></div><p class="hint" style="margin:8px 2px 0">Se muestran las 15 tiendas con mayor impacto ese día. Haz clic en una fila para abrir su plan.</p>`;
  $('#planBody').querySelectorAll('tr.click').forEach(tr => tr.onclick = () => { state.store = tr.dataset.s; $('#store').value = tr.dataset.s; refresh(true); window.scrollTo({ top: 0, behavior: reduce ? 'auto' : 'smooth' }); });
}

function renderStorePlan() {
  const day = state.plan.days.find(d => d.date === state.planDate); if (!day) { $('#planBody').innerHTML = '<p class="hint">Sin plan para este día.</p>'; return; }
  const st = day.staff, nAct = day.orders.length + day.promos.length;
  const col = (kind, title, n, inner, i) => `<div class="pcol rise" style="--d:${i * .06}s"><div class="pcol-h"><span class="ico ${kind}"><svg viewBox="0 0 24 24">${ICON[kind]}</svg></span>${title}<span class="chip n">${n}</span></div>${inner}</div>`;

  const orders = day.orders.map((o, i) => {
    const red = o.action === 'reduce';
    if (red) return `<div class="pi" style="--i:${i}"><div class="pi-t"><b>${esc(o.product)}</b><span class="tag blue">Reducir −${num(o.skip_units)}</span></div>
      <div class="pi-m"><span>Merma probable ${num(o.expected_waste_units, 0)} u. · vida útil ${num(o.shelf_life_days, 0)} d</span><span class="impact">+${money(o.est_impact)}</span></div>
      ${recBtns(o)}</div>`;
    return `<div class="pi" style="--i:${i}"><div class="pi-t"><b>${esc(o.product)}</b><span class="qty">×${num(o.order_qty)}</span></div>
      <div class="pi-m"><span class="dot p${o.priority}" title="Prioridad ${o.priority}"></span>
        <span>llega ${esc(dShort(o.arrives))} · ${o.cases >= 1 ? num(o.cases, 0) + ' caja' + (o.cases >= 2 ? 's' : '') + ' · ' : ''}cobertura ${num(o.cover_days, 1)} d</span><span class="impact">+${money(o.est_impact)}</span></div>
      <div class="pi-s">${esc(o.supplier)}${o.stockout_risk ? ' · <b class="warn">riesgo de quiebre</b>' : ''}</div></div>`;
  }).join('') || '<p class="hint empty">Sin pedidos hoy: el inventario cubre la demanda hasta el siguiente reparto.</p>';

  const promos = day.promos.map((p, i) => `<div class="pi" style="--i:${i}"><div class="pi-t"><b>${esc(p.product)}</b><span class="disc">−${Math.round(p.discount * 100)}%</span></div>
      <div class="pi-m"><span>día ${p.day_index} de ${p.days} · ${esc(dRange(p.start_date, p.end_date))}</span>${p.day_index === 1 ? `<span class="impact">+${money(p.est_impact)}</span>` : ''}</div>
      <div class="pi-s">${p.units_with_promo != null ? `Se venden ~${num(p.units_with_promo, 0)} u. con promo vs ${num(p.units_without, 0)} sin ella · merma ${num(p.waste_with, 0)} vs ${num(p.waste_without, 0)}` : ''}</div>
      ${recBtns(p, true)}</div>`).join('') || '<p class="hint empty">Sin promociones activas este día.</p>';

  let staff;
  if (st) {
    const mx = Math.max(st.people_required, st.people_baseline, 1);
    const chip = st.status === 'short' ? `<span class="tag coral">Agregar ${num(st.delta_hours, 0)} h</span>` : st.status === 'over' ? `<span class="tag blue">Reducir ${num(-st.delta_hours, 0)} h</span>` : '<span class="tag teal">Equilibrado</span>';
    staff = `<div class="big"><span class="bn" id="ppl">0</span><span class="bl">personas en turno<br><small>promedio sobre ${num(st.open_hours, 0)} h de apertura</small></span>${chip}</div>
      <div class="cmp"><div class="cmp-r"><span>Necesarias</span><div class="track"><div class="fill" data-w="${st.people_required / mx * 100}" style="background:var(--over)"></div></div><b>${num(st.people_required, 1)}</b></div>
        <div class="cmp-r"><span>Habitual</span><div class="track"><div class="fill" data-w="${st.people_baseline / mx * 100}" style="background:rgba(125,140,163,.6)"></div></div><b>${num(st.people_baseline, 1)}</b></div></div>
      <dl class="kv"><div><dt>Ventas previstas</dt><dd>${money(st.forecast_sales)}</dd></div><div><dt>Horas necesarias</dt><dd>${num(st.required_hours, 0)} h</dd></div>
        <div><dt>Horas habituales</dt><dd>${num(st.baseline_hours, 0)} h</dd></div><div><dt>Impacto</dt><dd>${st.est_impact ? '+' + money(st.est_impact) : '—'}</dd></div></dl>
      <p class="hint">Meta: ${num(st.target_splh, 0)} USD de venta por hora-labor (mediana histórica de la tienda). Habitual = promedio de las últimas 4 semanas del mismo día.</p>`;
  } else staff = '<p class="hint empty">Sin plan de personal (tienda cerrada o sin datos).</p>';

  $('#planBody').innerHTML = `<div class="phead"><h3>${esc(dLong(state.planDate))}</h3>
      <div class="pstats"><a class="chip act" href="/api/plan/orders.csv?store_id=${state.store}&date=${state.planDate}" title="Líneas de pedido de este día">⬇ Pedidos CSV</a><button class="chip act" onclick="window.print()" title="Imprimir el plan del día">🖨 Imprimir</button><span class="chip">${nAct} acciones de inventario y promoción</span><span class="impact" title="Suma de impactos estimados del día">+${money(day.impact)} potencial</span></div></div>
    <div class="pgrid">${col('inventory', 'Reponer', day.orders.length, orders, 0)}${col('promotion', 'Promocionar', day.promos.length, promos, 1)}${col('staffing', 'Personal', st ? num(st.people_required, 1) + ' pers.' : '—', staff, 2)}</div>`;
  if (st) countUp($('#ppl'), st.people_required, (x) => num(x, 1));
  requestAnimationFrame(() => $('#planBody').querySelectorAll('.fill').forEach(f => f.style.width = f.dataset.w + '%'));
}
function recBtns(x, withCopy) {
  if (!x.rec_id) return '';
  if (x.rec_status === 'approved') return '<div class="acts"><span class="tag teal">Aprobada</span></div>';
  return `<div class="acts"><button class="btn solid sm" onclick="planAct(${x.rec_id},'approve')">Aprobar</button><button class="btn sm" onclick="planAct(${x.rec_id},'dismiss')">Descartar</button>` +
    (withCopy ? `<button class="btn sm" onclick="planCopy(${x.rec_id})">Texto de cartel (Gemini)</button>` : '') + `</div><div id="pcopy${x.rec_id}"></div>`;
}
async function planAct(id, a) {
  await post(`/recommendations/${id}/${a}`);
  toast(a === 'approve' ? 'Acción aprobada' : 'Recomendación descartada');
  await loadPlan(); loadKpis(false); loadRecs();
}
async function planCopy(id) {
  const r = await post('/ai/promo-copy/' + id);
  $('#pcopy' + id).innerHTML = `<div class="copy">ES: ${esc(r.es)}<br>EN: ${esc(r.en)}${r.social ? '<br>Social: ' + esc(r.social) : ''}<br><span class="hint">${esc(r.source)}</span></div>`;
}
window.planAct = planAct; window.planCopy = planCopy;

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


/* ------------------------------------------------ backtest: "replay the past" */
async function loadBacktest() {
  const body = $('#btBody');
  let r;
  try { r = await api('/backtest'); } catch (e) { body.innerHTML = '<p class="muted">No se pudo cargar la comparación.</p>'; return; }
  if (r.status !== 'ready') {
    body.innerHTML = '<p class="muted">Calculando la comparación con el pasado… vuelve a abrir en un minuto.</p>';
    clearTimeout(loadBacktest.t); loadBacktest.t = setTimeout(loadBacktest, 15000); return;
  }
  const a = r.actual, o = r.ours, s = r.saved, w = r.window;
  $('#btHint').textContent = `${w.days} días (${dRange(w.start, w.end)}) · ${w.pairs.toLocaleString('es-MX')} producto×tienda · misma demanda, mismo inventario inicial`;
  const pct = (x, y) => y ? Math.round(100 * (x - y) / y) : 0;
  const tile = (lbl, real, ours, good) => `<div class="kv"><span>${lbl}</span><b>${money(ours)}</b><small class="${good ? 'up' : 'dn'}">vs ${money(real)} real (${pct(ours, real) > 0 ? '+' : ''}${pct(ours, real)}%)</small></div>`;
  body.innerHTML = `
    <div class="btk">
      ${tile('Merma (producto caducado)', a.waste_usd, o.waste_usd, o.waste_usd <= a.waste_usd)}
      ${tile('Margen perdido por faltantes', a.lost_margin_usd, o.lost_margin_usd, o.lost_margin_usd <= a.lost_margin_usd)}
      ${tile('Valor medio de inventario', a.avg_stock_value_usd, o.avg_stock_value_usd, o.avg_stock_value_usd <= a.avg_stock_value_usd)}
      <div class="kv"><span>Resultado neto</span><b class="${s.net_usd >= 0 ? 'up' : 'dn'}">${s.net_usd >= 0 ? '+' : '−'}${money(Math.abs(s.net_usd))}</b><small>Disponibilidad: ${(o.fill_rate * 100).toFixed(1)}% vs ${(a.fill_rate * 100).toFixed(1)}% real</small></div>
    </div>
    <div class="chart" style="height:220px"><canvas id="btChart" role="img" aria-label="Pérdida acumulada (merma más margen perdido): real contra nuestra política"></canvas></div>
    <p class="muted small">Pérdida acumulada = merma + margen perdido. Lo importante es el compromiso: nuestra política mantiene menos inventario y tira menos producto, a cambio de aceptar más faltantes (la disponibilidad baja). Las dos líneas se simulan con las mismas reglas (FIFO, vida útil), así que solo cambian las decisiones de pedido.
    Las entregas reales ocurrieron todos los días de la semana, por eso el reemplazo puede pedir cualquier día. La demanda "real" es una estimación (ventas corregidas por horas sin stock).
    Error del modelo en esta ventana: ${(r.forecast.wape_model * 100).toFixed(1)}% (promedio simple: ${(r.forecast.wape_naive * 100).toFixed(1)}%). Control: el simulador reproduce ${a.expired_units.toLocaleString('es-MX')} u. caducadas con la política real; lo que ocurrió fueron ${r.observed.expired_units.toLocaleString('es-MX')} u.</p>`;
  state.charts.bt?.destroy();
  const ink = css('--ink3'), grid = 'rgba(120,145,180,.16)';
  state.charts.bt = new Chart($('#btChart').getContext('2d'), {
    type: 'line',
    data: { labels: r.daily.dates.map(dShort), datasets: [
      { label: 'Real', data: r.daily.actual, borderColor: css('--slate'), borderWidth: 2, pointRadius: 0, tension: .25 },
      { label: 'Nuestra política', data: r.daily.ours, borderColor: css('--accent'), borderWidth: 2.4, pointRadius: 0, tension: .25 },
    ] },
    options: { responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
      plugins: { legend: { labels: { color: ink, usePointStyle: true, boxWidth: 8 } }, tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${money(c.parsed.y)}` } } },
      scales: { x: { ticks: { color: ink, maxTicksLimit: 8 }, grid: { display: false } }, y: { ticks: { color: ink, callback: (v) => kmoney(v) }, grid: { color: grid } } } },
  });
}


/* ------------------------------------------------ assumptions & limits */
async function loadAssump() {
  let c; try { c = await api('/config'); } catch (e) { return; }
  const a = c.assumptions || {};
  const items = [
    ['Moneda', `Todo está en ${c.currency}, tal como viene en el dataset. El margen es precio de lista − costo unitario.`],
    ['Sueldo por hora', `${money(a.wage_per_hour)} (el dataset no trae sueldos; es configurable con WAGE_PER_HOUR).`],
    ['Meta de ventas por hora-persona', `Mediana de cada tienda; se tolera ±${Math.round((a.staff_tolerance || 0) * 100)}% antes de recomendar cambios.`],
    ['Ventas perdidas por falta de personal', `${Math.round((a.understaff_leakage || 0) * 100)}% de la demanda por cada hora faltante. Es un supuesto, no un dato medido.`],
    ['Nivel de servicio en inventario', `z = ${a.service_z} (≈90%) para productos de larga vida. En perecederos se minimiza el costo esperado entre merma y faltante.`],
    ['Costo de mantener inventario', `${((a.carry_cost_per_day || 0) * 100).toFixed(1)}% del costo por día.`],
    ['Pedidos ya abiertos', 'Se asume que llegan a la mitad del plazo del proveedor.'],
    ['Calendario de entregas', 'El plan respeta el calendario del proveedor. En el histórico se recibió mercancía todos los días, así que el calendario del maestro no se cumple del todo.'],
    ['Demanda real', 'Es una estimación: ventas corregidas por las horas sin stock. Nunca se observa la demanda que no se vendió.'],
    ['Reproducir el pasado', 'Compara decisiones de pedido en un simulador con las mismas reglas para ambas; no prueba el efecto de aplicarlas en tienda real.'],
  ];
  $('#assumpBody').innerHTML = '<dl>' + items.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('') + '</dl>';
}
