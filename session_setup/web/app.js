// SPDX-License-Identifier: LGPL-3.0-or-later
// QA result page: reads /api/state and draws the QA ROOT file with JSROOT from the session image.
import { openFile, draw, cleanup, createTGraph, createHistogram } from '/jsroot/modules/main.mjs';
import { startView, viewActive, viewLanguage, refreshLive, setMode as setViewMode } from './view.js';
import { startControl, controlState, controlLanguage, setMode as setControlMode } from './control.js';
import { startSetup, sessionTab, sessionLanguage, setMode as setSessionMode } from './session.js';
import { startNestdaq, nestdaqTab, nestdaqLanguage, setMode as setNestdaqMode } from './nestdaq.js';
// SPDX-License-Identifier: LGPL-3.0-or-later
// Give every displayed command the same copy control without changing its text.
function installCommandCopy(t) {
  const selector = 'code.cmd, #check-command, #show-command, #start-command, #setting-command';
  function scan() {
    for (const code of document.querySelectorAll('code')) {
      if (code.hasAttribute('data-command-source')) continue;
      if (!code.matches(selector) && !/^(scripts\/|docker\s)/.test(code.textContent.trim())) continue;
      if (code.parentElement?.classList.contains('command-box')) continue;
      const box = document.createElement('span'); box.className = 'command-box';
      const button = document.createElement('button'); button.type = 'button'; button.className = 'copy-command';
      button.setAttribute('aria-label', t('copy_command'));
      button.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"></rect><path d="M5 15V6a2 2 0 0 1 2-2h9"></path></svg>';
      button.addEventListener('click', () => navigator.clipboard.writeText(code.textContent));
      code.replaceWith(box); box.append(code, button);
    }
    for (const button of document.querySelectorAll('.copy-command')) button.setAttribute('aria-label', t('copy_command'));
  }
  new MutationObserver(records => {
    if (records.some(record => (record.target instanceof HTMLElement && record.target.matches('code')) ||
        [...record.addedNodes].some(node => node instanceof HTMLElement && (node.matches('code') || node.querySelector('code'))))) scan();
  }).observe(document.body, { childList:true, subtree:true });
  scan();
  return scan;
}


// The page text lives in text/en.json, text/ko.json and text/ja.json, one item per line.
const LANGUAGES = ['en', 'ko', 'ja'];
const TEXT = {};
async function loadText(l) {
  if (!TEXT[l]) TEXT[l] = await (await fetch(`/text/${l}.json`, { cache: 'no-store' })).json();
  return TEXT[l];
}
const params = new URLSearchParams(location.search);
// Without a session the page opens at the setup tab; a session is chosen at the top of the page.
const session = params.get('session') || null;
let lang = params.get('lang') || (navigator.language || 'en').slice(0, 2);
if (!LANGUAGES.includes(lang)) lang = 'en';
let state = null, drawnFor = null, rootFile = null, busy = false;
const t = key => (TEXT[lang] || {})[key] ?? key;
const updateCommandCopies = installCommandCopy(t);
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  for (const c of children) node.append(c);
  return node;
};
const num = v => (v === null || v === undefined) ? '-' : String(v);
const countValue = v => (v === null || v === undefined) ? '-' : Number(v).toLocaleString('en-US');
const fixed = (v, d) => (v === null || v === undefined) ? '-' : Number(v).toFixed(d);

function applyLanguage() {
  document.documentElement.lang = lang;
  document.title = t('title');
  for (const node of document.querySelectorAll('[data-t]')) node.textContent = t(node.dataset.t);
  for (const b of document.querySelectorAll('[data-lang]')) b.classList.toggle('active', b.dataset.lang === lang);
  for (const node of document.querySelectorAll('[data-placeholder]')) node.placeholder = t(node.dataset.placeholder);
  const links = document.getElementById('qa-links');
  links.replaceChildren(...['alignment','numbers','hitmaps','correlations','tracks','dut','history'].map(key => el('a', { href:'#'+key }, t(key))));
  for (const b of document.querySelectorAll('.show-plots')) b.textContent = b.dataset.expanded === 'yes' ? t('show_fewer') : t('show_all').replace('{n}', b.dataset.count);
  updateCommandCopies();
  if (state) renderText();
  viewLanguage();
  controlLanguage();
  sessionLanguage();
  nestdaqLanguage();
  renderNdQa();
}

let ndQaMode = false, ndQaTimer = null, ndQaBusy = false, ndQaData = null, ndQaError = null;
function renderNdQa() {
  const table = (id, headings, rows) => {
    const head = el('tr', {}, ...headings.map(key => el('th', {'data-t':key}, t(key))));
    document.getElementById(id).replaceChildren(head, ...rows.map(row => el('tr', {}, ...row.map(value => el('td', {}, String(value))))));
  };
  const error = document.getElementById('nd-qa-error');
  error.hidden = !ndQaError; error.textContent = ndQaError ? t('nd_qa_unavailable') + ' ' + ndQaError : '';
  const data = ndQaData || {};
  document.getElementById('nd-qa-command').textContent = data.command || `scripts/session_control.sh ${session || 'NAME'} nestdaq qa --json`;
  table('nd-qa-msensors', ['nd_msensor','nd_qa_frames','nd_qa_triggers','nd_qa_hits'], (data.msensors || []).map(x => [x.name,x.frames,x.triggers,x.hits]));
  const pairs = data.pairs || [];
  document.getElementById('nd-qa-none').hidden = !!pairs.length || !!ndQaError;
  document.getElementById('nd-qa-pairs-wrap').hidden = !pairs.length || !!ndQaError;
  table('nd-qa-pairs', ['nd_msensor','nd_qa_matched','nd_qa_only_ref','nd_qa_only_dev','nd_qa_skipped','nd_qa_dcol','nd_qa_drow','nd_qa_drift','nd_qa_offset'],
    pairs.map(x => [x.msensor,x.matched,x.only_reference,x.only_msensor,x.skipped,Number(x.dcol_px).toFixed(2),Number(x.drow_px).toFixed(2),Number(x.drift_ppm).toFixed(3),Number(x.offset_us).toFixed(1)]));
}
async function readNdQa() {
  clearTimeout(ndQaTimer);
  if (!ndQaMode || document.body.dataset.tab !== 'qa' || document.hidden || ndQaBusy) return;
  ndQaBusy = true;
  try {
    const response = await fetch(`/api/nestdaq/qa?session=${encodeURIComponent(session)}`, {cache:'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'qa unavailable');
    ndQaData = data; ndQaError = null;
  } catch (err) { ndQaData = null; ndQaError = String(err); }
  renderNdQa(); ndQaBusy = false;
  if (ndQaMode && document.body.dataset.tab === 'qa' && !document.hidden) ndQaTimer = setTimeout(readNdQa, 5000);
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) readNdQa(); else clearTimeout(ndQaTimer); });

function card(label, value) { return el('div', { class: 'card' }, el('b', {}, t(label)), label === 'rc_state' ? el('span', { class:'rc-badge' + (value === 'RUNNING' ? ' running' : '') }, String(value)) : String(value)); }

function renderText() {
  controlState(state);
  const s = state, status = document.getElementById('status'), alert = document.getElementById('alert');
  const qa = s.qa;
  status.replaceChildren();
  alert.replaceChildren();
  if (!s.exists || !s.running) {
    alert.append(el('div', { class: 'alert warn' }, t(s.exists ? 'stopped' : 'missing')));
    document.getElementById('alignment').replaceChildren(); document.getElementById('numbers').replaceChildren();
    return;
  }
  status.append(card('run', num(s.run)), card('rc_state', num(s.rc_state)),
    card('beam', s.beam_on === null ? '-' : t(s.beam_on ? 'on' : 'off')), card('qa_time', qa?.finished_utc ? qa.finished_utc.slice(11, 19) + ' UTC' : '-'));
  document.getElementById('browse-run').textContent = `${t('run')} ${num(s.run)}`;
  const command = `scripts/align_session.sh ${s.session}`;
  if (!qa) alert.append(el('div', { class: 'alert warn' }, t('no_qa') + ' ' + s.session));
  else if (qa.status === 'not_aligned') alert.append(el('div', { class: 'alert bad', id: 'not-aligned' }, t('not_aligned') + ' ', el('code', {}, command)));
  else if (qa.status === 'failed') alert.append(el('div', { class: 'alert bad' }, t('failed_qa')));
  if (qa && qa.position_warning) alert.append(el('div', { class: 'alert warn', id: 'positions-changed' }, t('changed') + ' ', el('code', {}, command), el('div', {}, qa.position_warning)));
  const alignment = document.getElementById('alignment');
  alignment.replaceChildren();
  if (qa && qa.alignment) {
    const a = qa.alignment, rows = [el('tr', {}, el('th', {}, t('detector')), el('th', {}, t('correction')), el('th', {}, t('residual')))];
    for (const [name, c] of Object.entries(a.corrections_um || {})) {
      const r = (qa.residual_means_um || {})[name] || {};
      rows.push(el('tr', {}, el('td', {}, name), el('td', {}, `${fixed(c[0], 1)}, ${fixed(c[1], 1)}`), el('td', {}, `${fixed(r.x, 1)}, ${fixed(r.y, 1)}`)));
    }
    alignment.append(el('p', {}, `${t('aligned_with')}: ${a.id} (${a.run})`), el('p', {}, `${t('per_event')}: ${fixed(a.tracks_per_event_before, 3)} / ${fixed(a.tracks_per_event_after, 3)}`), el('table', { id: 'alignment-table' }, ...rows));
  } else alignment.append(el('p', {}, s.offset_max_mm ? t('none') : t('not_needed')));
  const numbers = document.getElementById('numbers');
  numbers.replaceChildren();
  if (qa && qa.status !== 'not_aligned') {
    const rows = [el('tr', {}, el('th', {}, t('events')), el('td', { id: 'n-events' }, countValue(qa.data_events))),
                  el('tr', {}, el('th', {}, t('tracks_n')), el('td', { id: 'n-tracks' }, countValue(qa.tracks)))];
    for (const e of qa.efficiency || []) rows.push(el('tr', {}, el('th', {}, `${t('efficiency')} ${e.dut}`), el('td', { class: 'n-efficiency' },
      el('span', { class:'eff-value' }, `${fixed(e.percent, 2)} %`), ' ', el('span', { class:'eff-error' }, `+${fixed(e.plus, 2)} / −${fixed(e.minus, 2)}`),
      el('div', { class:'eff-count' }, t('tracks_matched').replace('{matched}',countValue(e.matched)).replace('{total}',countValue(e.total))))));
    for (const line of qa.tiles_off || []) rows.push(el('tr', {}, el('th', {}, t('tiles_off')), el('td', {}, line.split(': ').pop() + ' (' + line.split(' ')[3] + ')')));
    numbers.append(el('table', { id: 'numbers-table' }, ...rows));
  }
  for (const box of document.querySelectorAll('.figure .label')) box.textContent = labelText(JSON.parse(box.dataset.item));
  for (const box of document.querySelectorAll('.plot .empty[data-kind]')) box.textContent = t(box.dataset.kind);
}

function labelText(item) {
  if (item.group === 'hitmaps') return item.detector;
  if (item.group === 'correlations') return `${item.detector} ${t(item.axis)}`;
  if (item.group === 'tracks') return 'chi2/ndf';
  if (item.group === 'dut') return `${item.detector} ${t(item.kind)}`;
  return t('history_plot');
}

function entries(obj) {
  if (!obj) return 0;
  if (obj._typename === 'TEfficiency') return obj.fTotalHistogram ? obj.fTotalHistogram.fEntries : 0;
  return obj.fEntries ?? (obj.fNpoints ?? 0);
}

// Show only the bins that hold entries (with a margin); the histogram contents stay as they are.
function zoomToContent(obj) {
  const axis = obj.fXaxis, n = axis ? axis.fNbins : 0, bins = obj.fArray || [];
  let first = 0, last = 0;
  for (let i = 1; i <= n; i++) if (bins[i] > 0) { if (!first) first = i; last = i; }
  if (!first) return;
  const margin = Math.max(2, Math.round((last - first + 1) * 0.25));
  axis.fFirst = Math.max(1, first - margin);
  axis.fLast = Math.min(n, last + margin);
  axis.fBits |= (1 << 11);  // ROOT's TAxis kAxisRange: the display range is set
}

function count(key) { document.body.dataset[key] = String(Number(document.body.dataset[key]) + 1); }

async function drawPlan() {
  if (busy) return;
  busy = true;
  const qa = state.qa;
  for (const id of ['hitmaps', 'correlations', 'tracks', 'dut', 'history']) {
    const grid = document.getElementById(id);
    for (const node of grid.querySelectorAll('.plot')) cleanup(node);
    if (grid.nextElementSibling?.classList.contains('show-plots')) grid.nextElementSibling.remove();
    grid.replaceChildren();
  }
  Object.assign(document.body.dataset, { drawn: '0', empty: '0', failed: '0', planned: '0', ready: 'no' });
  if (!state.running || !qa || qa.status !== 'ok' || !qa.histograms) { document.body.dataset.ready = 'yes'; busy = false; renderText(); return; }
  document.body.dataset.planned = String(state.plots.length);
  // The request names the QA result shown here, so the server never hands out another result's file.
  rootFile = await openFile(`/api/histograms.root?session=${encodeURIComponent(state.session)}&file=${encodeURIComponent(qa.histograms)}` +
                            `&qa=${encodeURIComponent(qa.id)}&finished=${encodeURIComponent(qa.finished_utc)}`).catch(() => null);
  for (const item of state.plots) {
    // The page's name tag sits above the drawing, so it never covers the histogram title.
    const box = el('div', { class: 'plot', 'data-group': item.group }), label = el('div', { class: 'label', 'data-item': JSON.stringify(item) }, labelText(item));
    // The path inside the QA ROOT file, to find the same histogram in an analysis of one's own.
    const where = item.path ? el('code', { class: 'path' }, item.path) : el('code', { class: 'path' }, '');
    document.getElementById(item.group).append(el('div', { class: 'figure' }, label, where, box));
    try {
      let obj = null;
      if (item.group === 'history') {
        const h = (state.history || []).filter(r => r.efficiency && r.efficiency.length);
        if (h.length) {
          obj = createTGraph(h.length, h.map((_, i) => i + 1), h.map(r => r.efficiency[0].percent));
          obj.fTitle = t('history_title');
          // Filled circles, so that even a single QA shows as a point.
          Object.assign(obj, { fMarkerStyle: 20, fMarkerSize: 1.4, fMarkerColor: 4, fLineColor: 4 });
          // A frame with room around the points, so a single point is not drawn on the frame's edge.
          const ys = h.map(r => r.efficiency[0].percent), low = Math.min(...ys), high = Math.max(...ys);
          const pad = Math.max(0.5, (high - low) * 0.25), frame = createHistogram('TH1F', 1);
          frame.fXaxis.fXmin = 0.5; frame.fXaxis.fXmax = h.length + 0.5;
          frame.fMinimum = low - pad; frame.fMaximum = high + pad; frame.fTitle = obj.fTitle;
          frame.fBits |= (1 << 9);  // TH1 kNoStats: the frame has no statistics box
          obj.fHistogram = frame;
          document.body.dataset.historypoints = String(h.length);
        }
      } else if (rootFile) obj = await rootFile.readObject(item.path).catch(() => null);
      if (!obj || (item.group !== 'history' && !entries(obj))) {
        box.append(el('div', { class: 'empty', 'data-kind': obj ? 'empty' : (item.group === 'history' ? 'empty' : 'failed') }, t(obj || item.group === 'history' ? 'empty' : 'failed')));
        count(obj || item.group === 'history' ? 'empty' : 'failed');
        continue;
      }
      if (item.group === 'tracks' || (item.kind || '').startsWith('residual_')) zoomToContent(obj);
      // Titles and statistics are shown in the card, not over the small plot.
      const originalTitle = obj.fTitle, originalBits = obj.fBits, frameTitle = obj.fHistogram?.fTitle, frameBits = obj.fHistogram?.fBits;
      obj.fTitle = ''; obj.fBits = (obj.fBits || 0) | (1 << 9);
      if (obj.fHistogram) { obj.fHistogram.fTitle = ''; obj.fHistogram.fBits = (obj.fHistogram.fBits || 0) | (1 << 9); }
      try { await draw(box, obj, item.group === 'history' ? 'APL' : item.option); }
      finally { obj.fTitle = originalTitle; obj.fBits = originalBits; if (obj.fHistogram) { obj.fHistogram.fTitle = frameTitle; obj.fHistogram.fBits = frameBits; } }
      count('drawn');
    } catch (err) {
      box.append(el('div', { class: 'empty', 'data-kind': 'failed' }, t('failed')));
      count('failed');
    }
  }
  document.body.dataset.ready = 'yes';
  busy = false;
  for (const id of ['hitmaps', 'correlations', 'tracks', 'dut', 'history']) {
    const grid = document.getElementById(id), figures = [...grid.querySelectorAll('.figure')];
    grid.nextElementSibling?.classList.contains('show-plots') && grid.nextElementSibling.remove();
    if (figures.length <= 4) continue;
    figures.forEach((figure, i) => { figure.hidden = i >= 4; });
    const button = el('button', { class:'show-plots', type:'button', 'data-count':String(figures.length), 'data-expanded':'no' }, t('show_all').replace('{n}', figures.length));
    button.addEventListener('click', () => {
      const expanded = button.dataset.expanded !== 'yes'; button.dataset.expanded = expanded ? 'yes' : 'no';
      figures.forEach((figure, i) => { figure.hidden = !expanded && i >= 4; });
      button.textContent = expanded ? t('show_fewer') : t('show_all').replace('{n}', figures.length);
    });
    grid.after(button);
  }
  renderText();
  buildTree();
}

async function buildTree() {
  const tree = document.getElementById('tree');
  tree.replaceChildren();
  if (!rootFile) return;
  const walk = async (path, keys, parent) => {
    let count = 0;
    for (const key of keys) {
      const full = path ? `${path}/${key.fName}` : key.fName;
      if (key.fClassName.startsWith('TDirectory')) {
        const list = el('ul', { hidden:'' }), button = el('button', { type:'button', 'aria-expanded':'false' }, key.fName);
        const item = el('li', { class:'dir', 'data-name':key.fName }, button, list); parent.append(item);
        const dir = await rootFile.readDirectory(full).catch(() => null);
        const children = dir ? await walk(full, dir.fKeys, list) : 0; count += children;
        button.textContent = `${key.fName} (${children})`;
        button.addEventListener('click', () => { list.hidden = !list.hidden; button.setAttribute('aria-expanded', String(!list.hidden)); });
      } else {
        const item = el('li', { 'data-path':full, 'data-name':key.fName, 'data-type':key.fClassName }, key.fName, el('span', { class:'tree-type badge' }, key.fClassName));
        item.addEventListener('click', () => showHistogram(full));
        parent.append(item); count++;
      }
    }
    return count;
  };
  await walk('', rootFile.fKeys, tree);
  document.body.dataset.histograms = String(tree.querySelectorAll('li[data-path]').length);
  const first = params.get('show');
  if (first) await showHistogram(first);
}

async function showHistogram(path) {
  const view = document.getElementById('browse-view');
  cleanup(view);
  view.replaceChildren();
  const obj = await rootFile.readObject(path).catch(() => null);
  if (!obj) { view.append(el('div', { class: 'empty' }, t('failed'))); return; }
  document.getElementById('browse-path').textContent = path;
  document.getElementById('browse-type').textContent = obj._typename || '';
  await draw(view, obj, obj._typename.startsWith('TH2') || obj._typename.startsWith('TProfile2') ? 'colz' : '');
  document.body.dataset.shown = path;
}

// The QA tabs read the session only while one of them is shown.
const qaTabs = ['qa', 'browse'];
const qaShown = () => qaTabs.some(id => document.getElementById(id).classList.contains('active'));

async function poll() {
  if (!qaShown()) return;
  if (!session) {
    controlState(null);
    document.getElementById('alert').replaceChildren(el('div', { class: 'alert warn', 'data-t': 'no_session_note' }, t('no_session_note')));
    document.body.dataset.ready = 'yes';
    return;
  }
  try {
    const response = await fetch(`/api/state?session=${encodeURIComponent(session)}`, { cache: 'no-store' });
    state = await response.json();
    const key = state.running && state.qa ? `${state.qa.id}|${state.qa.status}|${state.qa.finished_utc}` : `none|${state.running}`;
    renderText();
    if (key !== drawnFor) { drawnFor = key; await drawPlan(); }
  } catch (err) { /* The next poll tries again. */ }
}

for (const b of document.querySelectorAll('[data-lang]')) b.addEventListener('click', async () => { lang = b.dataset.lang; await loadText(lang); applyLanguage(); });
await loadText(lang);
startView({ session, t, el });
startControl({ session, t, el, refreshLive, refreshQa: () => poll() });
for (const b of document.querySelectorAll('[data-tab]')) b.addEventListener('click', () => {
  const was = qaShown();
  for (const x of document.querySelectorAll('[data-tab]')) x.classList.toggle('active', x === b);
  for (const x of document.querySelectorAll('.tab')) x.classList.toggle('active', x.id === b.dataset.tab);
  document.body.dataset.tab = b.dataset.tab;
  viewActive(b.dataset.tab === 'view');
  sessionTab(b.dataset.tab);
  nestdaqTab(b.dataset.tab);
  clearTimeout(ndQaTimer); readNdQa();
  if (!was && qaShown()) poll();
});
const firstTab = params.get('tab') || (session ? 'qa' : 'setup');
document.getElementById('tree-filter').addEventListener('input', event => {
  const term = event.target.value.trim().toLocaleLowerCase(), tree = document.getElementById('tree');
  for (const item of tree.querySelectorAll('li[data-path]')) item.hidden = !!term && !item.dataset.name.toLocaleLowerCase().includes(term);
  for (const item of [...tree.querySelectorAll('li.dir')].reverse()) {
    const list = item.querySelector(':scope > ul'), match = !!list.querySelector('li[data-path]:not([hidden])');
    item.hidden = !!term && !match;
    list.hidden = !term; item.querySelector(':scope > button').setAttribute('aria-expanded', String(!!term));
  }
});
startSetup({ session, t, el });
startNestdaq({ session, t, el });
document.querySelector(`[data-tab="${firstTab}"]`)?.click();
applyLanguage();

(async () => {
  let mode = 'eudaq';
  if (session) {
    try {
      const response = await fetch(`/api/daq-mode?session=${encodeURIComponent(session)}`, { cache: 'no-store' });
      if (response.ok && (await response.json()).mode === 'nestdaq') mode = 'nestdaq';
    } catch (err) { /* Use the existing mode. */ }
  }
  document.body.dataset.daqmode = mode;
  setControlMode(mode); setSessionMode(mode); setNestdaqMode(mode); setViewMode(mode);
  ndQaMode = mode === 'nestdaq'; document.getElementById('nd-qa').hidden = !ndQaMode; readNdQa();
  if (mode === 'eudaq' && document.body.dataset.tab === 'nestdaq') document.querySelector('[data-tab="view"]').click();
})();

await poll();
setInterval(poll, 5000);
