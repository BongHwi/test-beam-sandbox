// SPDX-License-Identifier: LGPL-3.0-or-later
// Setup tab (setup files of configs/setups: show, edit, check, save, start a session from one), the session
// choice, and the DAQ tab (the text of the session's screen windows, the RunControl keys R, T and Q, copying the
// output and removing the container). Every button runs one command through the page server and shows it.
import { Scene3D, refreshLive } from './view.js';
import { operate, canOperate, jobBlock, onControlChange, bindCommandChoice, setFinishHandlers } from './control.js';

const WINDOWS = ['rc', 'rp', 'dp', 'sp', 'dc', 'log'];
const KEYS = ['R', 'T', 'Q'];
const CONFIRM_KEYS = ['T', 'Q'];
const SCREEN_MS = 1000, CHECK_DELAY_MS = 300, FOLLOW_MS = 1000, SESSIONS_MS = 10000;
const NAME = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;
const FILE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.ini$/;

let ctx = null;              // { session, t, el, tab }
let list = null;             // the server's setup list: files, alignments, items, new
let draft = null;            // { file, sections: [[section, {item: text}]], dirty, loaded }
let checkResult = null, checkTimer = null, checkSeq = 0;
let preview = null, previewBuilt = false;
let setupShown = {}, confirmBox = null;
const openSections = new Set();
let setupCommandChoice = () => {}, keyCommandChoice = () => {};
let previewPlaceholder = null, previousOverflow = '';
let windowName = 'rc', screenTimer = null, screenBusy = false, daqActive = false, setupActive = false;
let daqShown = {}, sessionsList = [], built = false;
const body = document.body.dataset;

const cmd = parts => parts.join(' ');
const startCommand = () => {
  const parts = ['scripts/start_session.sh', '--setup', 'configs/setups/' + (draft ? draft.file : 'FILE')];
  const alignment = document.getElementById('start-alignment')?.value;
  if (alignment) parts.push('--alignment', 'configs/alignments/' + alignment);
  parts.push('--name', document.getElementById('start-name')?.value || 'NAME', '--no-attach');
  return cmd(parts);
};
const keyCommand = key => key === 'Q'
  ? cmd(['docker', 'exec', ctx.session || 'NAME', 'tmux', 'send-keys', '-t', 'ITS3:rc', key])
  : cmd(['scripts/session_control.sh', ctx.session || 'NAME', key === 'R' ? 'repeat' : 'terminate', '--id', key === 'R' ? 'page-repeat' : 'page-terminate']);
const copyCommand = () => cmd(['docker', 'cp', (ctx.session || 'NAME') + ':/tmp/session-run', 'outputs/' + (ctx.session || 'NAME') + '-UTCTIME']);
const removeCommand = () => cmd(['docker', 'rm', '-f', ctx.session || 'NAME']);

function go(session, tab) {
  // The token stays in this tab's storage, so the page keeps operating after moving to another session.
  const q = new URLSearchParams(location.search);
  if (session) q.set('session', session); else q.delete('session');
  q.set('tab', tab);
  q.delete('view');
  location.assign(location.pathname + '?' + q.toString());
}

// ---- session choice ----
async function readSessions() {
  try {
    const answer = await (await fetch('/api/sessions', { cache: 'no-store' })).json();
    sessionsList = answer.sessions || [];
  } catch (err) { /* The next reading tries again. */ }
  renderSessions();
  if (built) renderDaq();
}

function renderSessions() {
  const { t, el } = ctx;
  const select = document.getElementById('session-pick');
  const names = sessionsList.map(s => s.name);
  if (ctx.session && !names.includes(ctx.session)) names.push(ctx.session);
  const options = [el('option', { value: '', 'data-t': 'no_session' }, t('no_session'))];
  for (const name of names.sort()) {
    const s = sessionsList.find(x => x.name === name);
    options.push(el('option', { value: name }, s && s.state !== 'running' ? `${name} (${s.state})` : name));
  }
  select.replaceChildren(...options);
  select.value = ctx.session || '';
  body.sessions = String(sessionsList.length);
}

// ---- setup tab ----
const itemsOf = section => {
  const kind = section.split(' ')[0];
  return (list && list.items[kind]) || [];
};

function readoutFilter(section, values) {
  if (!section.startsWith('msensor')) return;
  const skip = values.readout === 'triggered' ? list.items.continuous_only : list.items.triggered_only;
  const keep = values.readout === 'triggered' ? list.items.triggered_only : list.items.continuous_only;
  for (const k of skip) delete values[k];
  for (const k of keep) if (!(k in values)) values[k] = list.items.msensor.find(i => i.item === k).default;
}

function complete(sections) {
  // Every item of each section, with the default where the file leaves it out (the saved file writes them all).
  for (const [section, values] of sections) {
    if (section.startsWith('scintillator ') && values.shape === 'disc') {
      if (!('size_mm' in values)) values.size_mm = '20';
      if (!('hole_mm' in values)) values.hole_mm = '0';
    }
    for (const item of itemsOf(section)) if (!(item.item in values) && !item.required) values[item.item] = item.default;
    readoutFilter(section, values);
  }
  return sections;
}

function deviceSections() { return draft.sections.filter(([s]) => /^(alpide|msensor) /.test(s)); }

function renumber(kind) {
  let n = 0;
  for (const entry of draft.sections) if (entry[0].split(' ')[0] === kind) entry[0] = `${kind} ${n++}`;
}

function addDevice(kind) {
  const devices = deviceSections();
  if (kind === 'scintillator') {
    const plates = draft.sections.filter(([s]) => s.startsWith('scintillator '));
    if (plates.length >= 4) return;
    const used = new Set(plates.map(([, v]) => Number(v.channel)));
    const channel = [0, 1, 2, 3].find(n => !used.has(n));
    const values = Object.fromEntries(list.items.scintillator.map(i => [i.item, i.default]));
    values.channel = String(channel); values.position_mm = '1.1, -2.1, 140';
    draft.sections.push([`scintillator ${plates.length}`, values]);
    openSections.add(`scintillator ${plates.length}`);
    if (!draft.sections.some(([s]) => s === 'trigger')) draft.sections.push(['trigger', Object.fromEntries(list.items.trigger.map(i => [i.item, i.default]))]);
    changed(true); return;
  }
  const planes = devices.map(([, v]) => parseInt(v.plane, 10)).filter(Number.isFinite);
  const zs = devices.map(([, v]) => parseFloat((v.position_mm || '').split(',')[2])).filter(Number.isFinite);
  const first = devices.length ? (devices[0][1].position_mm || '0, 0, 0').split(',').map(x => x.trim()) : ['0', '0'];
  const values = {};
  for (const item of list.items[kind]) values[item.item] = item.default;
  values.plane = String(planes.length ? Math.max(...planes) + 1 : 0);
  values.position_mm = `${first[0]}, ${first[1]}, ${zs.length ? Math.max(...zs) + 20 : 0}`;
  const number = devices.filter(([s]) => s.startsWith(kind + ' ')).length;
  draft.sections.push([`${kind} ${number}`, values]);
  openSections.add(`${kind} ${number}`);
  readoutFilter(`${kind} ${number}`, values);
  changed(true);
}

function removeDevice(section) {
  draft.sections = draft.sections.filter(([s]) => s !== section);
  renumber(section.split(' ')[0]);
  if (!draft.sections.some(([s]) => s.startsWith('scintillator '))) draft.sections = draft.sections.filter(([s]) => s !== 'trigger');
  changed(true);
}

function changed(rebuild) {
  ++checkSeq;
  document.getElementById('setup-optimiser').hidden = true;
  if (!draft.dirty && list.protected.includes(draft.loaded)) {
    draft.file = suggestName(draft.loaded);
    draft.saved = null;
  }
  draft.dirty = true;
  if (rebuild) renderEditor();
  renderSave();
  clearTimeout(checkTimer);
  checkTimer = setTimeout(runCheck, CHECK_DELAY_MS);
}

async function runCheck() {
  if (!draft) return;
  const seq = ++checkSeq;
  body.setupcheck = 'pending';
  let answer;
  try {
    const response = await fetch('/api/setup-check', { method: 'POST', cache: 'no-store', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file: draft.file, sections: draft.sections }) });
    answer = await response.json();
    if (!response.ok) answer = { code: 2, check: [answer.error + (answer.item ? ': ' + answer.item : '')], show: [], view: null };
  } catch (err) { answer = { code: 2, check: [String(err)], show: [], view: null }; }
  if (seq !== checkSeq) return;
  checkResult = answer;
  renderCheck();
  body.setupcheck = answer.code === 0 ? 'valid' : 'invalid';
}

function renderCheck() {
  const r = checkResult || { check: [], show: [] };
  const optimiser = document.getElementById('setup-optimiser');
  optimiser.hidden = r.code !== 0 || !r.view || !r.view.devices.length;
  if (!optimiser.hidden) {
    const devices = [...r.view.devices].sort((a, b) => a.position_mm[2] - b.position_mm[2]);
    const number = (value, digits) => String(Number(value.toFixed(digits)));
    const z = devices.map(d => d.position_mm[2]);
    const positions = [z[0] - 10, ...z, z[z.length - 1] + 10].map(v => number(v / 10, 3));
    const material = ['', ...devices.map(() => '0.00107'), ''];
    const enabled = ['0', ...devices.map(d => d.role === 'reference' ? '1' : '0'), '0'];
    const energy = r.view.beam.energy_gev;
    const momentum = number(Math.sqrt(energy * energy + 2 * energy * 0.00051099895), 4);
    for (const [axis, index] of [['x', 0], ['y', 1]]) {
      const resolution = ['', ...devices.map(d => number(d.pitch_um[index] / Math.sqrt(12) / 10000, 6)), ''];
      document.getElementById('optimiser-' + axis).href = 'https://mmager.web.cern.ch/telescope/tracking.html#' +
        `part=e&p=${momentum}&x=[${positions}]&XX0=[${material}]&sy=[${resolution}]&en=[${enabled}]`;
    }
  }
  const check = document.getElementById('setup-check');
  document.getElementById('check-badge').hidden = r.code !== 0;
  check.textContent = (r.check || []).join('\n');
  check.className = 'screen ' + (r.code === 0 ? 'ok' : 'bad');
  document.getElementById('setup-show').textContent = (r.show || []).join('\n');
  for (const line of r.code ? (r.check || []) : []) {
    const match = line.match(/\[((?:alpide|msensor|scintillator) \d+)\]/);
    if (!match) continue;
    openSections.add(match[1]);
    const fieldset = [...document.querySelectorAll('#setup-editor fieldset')].find(x => x.dataset.section === match[1]);
    const detail = fieldset?.querySelector('.device-fields'), toggle = fieldset?.querySelector('.device-summary');
    if (detail) detail.hidden = false;
    if (toggle) toggle.setAttribute('aria-expanded', 'true');
  }
  if (r.view && preview && preview.renderer) {
    preview.build(r.view, previewBuilt ? null : 'reset');
    previewBuilt = true;
    preview.render(true);
  }
  renderSave();
}

function input(section, item, values) {
  const { el } = ctx;
  const value = values[item.item] ?? '';
  let node;
  if (item.choices && item.choices.length) {
    node = el('select', { 'data-item': item.item }, ...item.choices.map(c => el('option', { value: c }, c)));
    node.value = value;
  } else node = el('input', { type: 'text', value, 'data-item': item.item, spellcheck: 'false' });
  node.addEventListener('input', () => {
    values[item.item] = node.value;
    if (item.item === 'readout') { readoutFilter(section, values); changed(true); return; }
    if (item.item === 'shape' && section.startsWith('scintillator ')) {
      values.size_mm = node.value === 'disc' ? (values.size_mm || '20').split(',')[0].trim() : ((values.size_mm || '20').includes(',') ? values.size_mm : `${values.size_mm}, ${values.size_mm}`);
      values.hole_mm = node.value === 'disc' ? (values.hole_mm || '0').split(',')[0].trim() : ((values.hole_mm || '0').includes(',') ? values.hole_mm : `${values.hole_mm}, ${values.hole_mm}`);
      changed(true); return;
    }
    if (['tiles', 'tiles_off'].includes(item.item)) { renderTiles(section, values); }
    changed(false);
  });
  node.addEventListener('change', () => { if (item.item === 'readout') return; values[item.item] = node.value; });
  return node;
}

function renderTiles(section, values) {
  const box = document.querySelector(`[data-tiles="${section}"]`);
  if (!box) return;
  const { el } = ctx;
  const grid = (values.tiles || '').split(',').map(x => parseInt(x, 10));
  const off = new Set((values.tiles_off || '').split(',').map(x => parseInt(x, 10)).filter(Number.isFinite));
  box.replaceChildren();
  if (grid.length !== 2 || !grid.every(n => n >= 1 && n <= 64) || grid[0] * grid[1] > 256) return;
  const [nx, ny] = grid;
  // Rows from the top: tile 0 is at column 0, row 0 (bottom left, as in the 3D view).
  for (let row = ny - 1; row >= 0; row--) {
    const line = el('div', { class: 'tile-row' });
    for (let col = 0; col < nx; col++) {
      const i = row * nx + col;
      const b = el('button', { class: 'tile' + (off.has(i) ? ' off' : ''), 'data-tile': String(i), type: 'button' }, String(i));
      b.addEventListener('click', () => {
        if (off.has(i)) off.delete(i); else off.add(i);
        values.tiles_off = [...off].sort((a, b2) => a - b2).join(', ');
        const field = document.querySelector(`[data-section="${section}"] [data-item="tiles_off"]`);
        if (field) field.value = values.tiles_off;
        renderTiles(section, values);
        changed(false);
      });
      line.append(b);
    }
    box.append(line);
  }
}

function renderEditor() {
  const { t, el } = ctx;
  const box = document.getElementById('setup-editor');
  if (!draft) { box.replaceChildren(el('p', { class: 'note', 'data-t': 'setup_choose' }, t('setup_choose'))); return; }
  const parts = [el('p', { class: 'note', 'data-t': 'setup_item_help' }, t('setup_item_help'))];
  const deviceCount = draft.sections.filter(([s]) => /^(alpide|msensor) /.test(s)).length;
  const scintCount = draft.sections.filter(([s]) => s.startsWith('scintillator ')).length;
  document.getElementById('setup-links').replaceChildren(...[
    ['run','setup_sections_run'],['beam','setup_sections_beam'],['positions','setup_sections_positions'],
    ['devices','setup_sections_devices',deviceCount],['scintillators','setup_sections_scintillators',scintCount],['trigger','setup_sections_trigger']
  ].map(([id,key,n]) => el('a',{href:'#setup-section-'+id},t(key)+(n === undefined ? '' : ` · ${n}`))));
  for (const [section, values] of draft.sections) {
    const kind = section.split(' ')[0];
    const known = itemsOf(section);
    const rows = [];
    for (const item of known) {
      if (!(item.item in values) && !item.required) continue;
      rows.push(el('div', { class: 'item' }, el('label', {}, el('code', {}, item.item)), input(section, item, values),
                   el('small', {}, `${item.help}; ${item.accepted}`)));
    }
    for (const key of Object.keys(values).filter(k => !known.some(i => i.item === k))) {
      rows.push(el('div', { class: 'item' }, el('label', {}, el('code', {}, key)),
                   input(section, { item: key, help: '', accepted: '' }, values), el('small', {}, '')));
    }
    const isDevice = /^(alpide|msensor|scintillator) /.test(section);
    const titleKey = {run:'setup_sections_run',beam:'setup_sections_beam',positions:'setup_sections_positions',trigger:'setup_sections_trigger'}[section];
    const head = [titleKey ? el('span', { 'data-t':titleKey }, t(titleKey)) : el('code', {}, `[${section}]`)];
    let remove = null;
    if (/^(alpide|msensor|scintillator) /.test(section)) {
      remove = el('button', { class: 'edit small', type: 'button', 'data-remove': section, 'data-t': 'setup_remove_device' }, t('setup_remove_device'));
      remove.addEventListener('click', () => removeDevice(section));
    }
    const legend = el('legend', isDevice ? { class:'visually-hidden' } : {}, ...head);
    const fieldset = el('fieldset', { 'data-section': section, id:'setup-section-'+(isDevice ? section.replace(' ','-') : section) }, legend);
    let content = fieldset;
    if (isDevice) {
      const role = kind === 'alpide' ? 'reference' : kind === 'msensor' ? (values.role || 'dut') : 'trigger';
      const summary = el('button', { class:'device-summary', type:'button', 'aria-expanded':String(openSections.has(section)) },
        el('span', { class:'summary-text' }, `${section} · ${values.plane ?? values.channel ?? '-'} · z ${(values.position_mm || '').split(',').pop().trim()} · ${values.readout || values.ithr || values.shape || ''}`),
        el('span', { class:'role-badge '+role }, role === 'dut' ? t('dut_role') : role === 'reference' ? t('role_reference_badge') : 'trigger'));
      content = el('div', { class:'device-fields', ...(openSections.has(section) ? {} : { hidden:'' }) }, el('div', { class:'fields' }, ...rows));
      summary.addEventListener('click', () => { content.hidden = !content.hidden; summary.setAttribute('aria-expanded',String(!content.hidden)); if (content.hidden) openSections.delete(section); else openSections.add(section); });
      fieldset.append(el('div', { class:'device-row' }, summary, remove),content);
    } else fieldset.append(el('div', { class:'fields' }, ...rows));
    if (kind === 'msensor') {
      content.append(el('p', { class: 'note', 'data-t': 'setup_tiles_click' }, t('setup_tiles_click')), el('div', { class: 'tiles', 'data-tiles': section }));
    }
    parts.push(fieldset);
  }
  const addA = el('button', { class: 'edit', type: 'button', id: 'add-alpide', 'data-t': 'setup_add_alpide' }, t('setup_add_alpide'));
  const addM = el('button', { class: 'edit', type: 'button', id: 'add-msensor', 'data-t': 'setup_add_msensor' }, t('setup_add_msensor'));
  addA.addEventListener('click', () => addDevice('alpide'));
  addM.addEventListener('click', () => addDevice('msensor'));
  const addS = el('button', { class: 'edit', type: 'button', id: 'add-scintillator', 'data-t': 'setup_add_scintillator' }, t('setup_add_scintillator'));
  addS.disabled = draft.sections.filter(([s]) => s.startsWith('scintillator ')).length >= 4;
  addS.addEventListener('click', () => addDevice('scintillator'));
  parts.push(el('div', { class: 'op-row' }, addA, addM, addS));
  box.replaceChildren(...parts);
  const firstDevice = box.querySelector('[data-section^="alpide "], [data-section^="msensor "], [data-section^="scintillator "]');
  if (firstDevice) { const anchor = el('h2', { id:'setup-section-devices', 'data-t':'setup_sections_devices' }, t('setup_sections_devices')); firstDevice.before(anchor); }
  const firstScint = box.querySelector('[data-section^="scintillator "]');
  const scintAnchor = el('h2', { id:'setup-section-scintillators', 'data-t':'setup_sections_scintillators' }, t('setup_sections_scintillators'));
  (firstScint || addS.parentElement).before(scintAnchor);
  if (!box.querySelector('#setup-section-trigger')) box.append(el('span', { id:'setup-section-trigger' }));
  for (const [section, values] of draft.sections) if (section.startsWith('msensor')) renderTiles(section, values);
}

function renderFiles() {
  const { t, el } = ctx;
  const ul = document.getElementById('setup-files');
  ul.replaceChildren(...(list ? list.files : []).map(f => {
    const b = el('button', { class: 'file' + (draft && draft.loaded === f.name ? ' active' : ''), type: 'button', 'data-file': f.name }, el('span',{},f.name));
    if (f.protected) {
      const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns,'svg'), path = document.createElementNS(ns,'path');
      for (const [key,value] of Object.entries({width:'16',height:'16',viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':'1.5','stroke-linecap':'round','stroke-linejoin':'round',role:'img','aria-label':t('setup_repository')})) svg.setAttribute(key,value);
      path.setAttribute('d','M6 10h12v10H6z M8 10V7a4 4 0 0 1 8 0v3');svg.append(path);b.append(svg);
    }
    b.addEventListener('click', () => openFile(f.name));
    return el('li', {}, b, f.protected ? el('small', { 'data-t': 'setup_repository', hidden:'' }, t('setup_repository')) : '');
  }));
}

function openPreview() {
  if (previewPlaceholder) return;
  const card = document.querySelector('.setup-preview-card'), dialog = document.getElementById('preview-dialog');
  previewPlaceholder = document.createElement('div');
  previewPlaceholder.style.height = card.getBoundingClientRect().height + 'px';
  card.before(previewPlaceholder); dialog.append(card); dialog.hidden = false; card.classList.add('expanded');
  document.getElementById('preview-expand').hidden = true; document.getElementById('preview-close').hidden = false;
  dialog.setAttribute('aria-label', ctx.t('setup_preview'));
  previousOverflow = document.body.style.overflow; document.body.style.overflow = 'hidden';
  document.getElementById('preview-close').focus();
  requestAnimationFrame(() => { preview.render(true); });
}

function closePreview() {
  if (!previewPlaceholder) return;
  const card = document.querySelector('.setup-preview-card');
  card.classList.remove('expanded'); previewPlaceholder.replaceWith(card); previewPlaceholder = null;
  document.getElementById('preview-dialog').hidden = true;
  document.getElementById('preview-close').hidden = true; document.getElementById('preview-expand').hidden = false;
  document.body.style.overflow = previousOverflow;
  requestAnimationFrame(() => { preview.render(true); document.getElementById('preview-expand').focus(); });
}

function previewKeys(event) {
  if (!previewPlaceholder) return;
  if (event.key === 'Escape') { event.preventDefault(); closePreview(); return; }
  if (event.key !== 'Tab') return;
  const dialog = document.getElementById('preview-dialog');
  const focusable = [...dialog.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), a[href]')]
    .filter(node => node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden');
  if (!focusable.length) { event.preventDefault(); return; }
  const first = focusable[0], last = focusable[focusable.length - 1];
  if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
}

function suggestName(name) {
  // An edit of a repository file is saved under another name.
  if (!list || !list.protected.includes(name)) return name;
  const stem = name.replace(/\.ini$/, '');
  for (let n = 1; ; n++) if (!list.files.some(f => f.name === `${stem}-${n}.ini`)) return `${stem}-${n}.ini`;
}

async function openFile(name) {
  const seq = ++checkSeq;
  clearTimeout(checkTimer);
  document.getElementById('setup-optimiser').hidden = true;
  const answer = await (await fetch(`/api/setup?file=${encodeURIComponent(name)}`, { cache: 'no-store' })).json();
  if (seq !== checkSeq) return;
  const sections = answer.sections ? complete(answer.sections) : complete(JSON.parse(JSON.stringify(list.new)));
  openSections.clear();
  draft = { file: name, sections, dirty: !answer.sections, loaded: name, saved: answer.sections ? name : null };
  checkResult = { code: answer.code, check: answer.check, show: answer.show, view: answer.view };
  setupShown = {};
  renderFiles(); renderEditor(); renderCheck();
  body.setupfile = name;
}

function newSetup() {
  // The values of scripts/setup_session.sh new --no-input.
  let n = 1;
  while (list.files.some(f => f.name === `setup-${n}.ini`)) n++;
  openSections.clear();
  draft = { file: `setup-${n}.ini`, sections: complete(JSON.parse(JSON.stringify(list.new))), dirty: true, loaded: null, saved: null };
  setupShown = {};
  renderFiles(); renderEditor(); changed(false);
  body.setupfile = '';
}

async function save(overwrite) {
  if (!draft) return;
  confirmBox = null;
  const name = document.getElementById('setup-name').value.trim();
  draft.file = name;
  if (!FILE.test(name)) { setupShown.save = { message: 'setup_bad_name' }; renderSave(); return; }
  if (list.protected.includes(name)) { setupShown.save = { message: 'setup_protected' }; renderSave(); return; }
  setupShown.save = { pending: true };
  renderSave();
  const { status, answer } = await operate('save_setup', { file: name, sections: draft.sections, overwrite: !!overwrite });
  if (status === 409 && answer.error === 'exists') { confirmBox = 'overwrite'; setupShown.save = null; }
  else if (status === 422) setupShown.save = { invalid: answer.lines || [] };
  else if (status === 200) {
    draft.dirty = false; draft.saved = name; draft.loaded = name;
    setupShown.save = { job: answer.job };
    await readList();
  } else setupShown.save = { rejected: answer.error || String(status) };
  renderSave();
  body.setupsaved = status === 200 ? name : '';
}

async function runStart() {
  if (!draft || !draft.saved) return;
  const name = document.getElementById('start-name').value.trim();
  if (!NAME.test(name)) { setupShown.start = { message: 'start_bad_name' }; renderSave(); return; }
  setupShown.start = { pending: true, command: startCommand() };
  renderSave();
  const { status, answer } = await operate('start_session', { session: name, setup: draft.saved,
                                                             alignment: document.getElementById('start-alignment').value });
  if (status === 409 && answer.error === 'image') { setupShown.start = { image: answer }; renderSave(); return; }
  if (status === 409) { setupShown.start = { busy: true, job: answer.job }; renderSave(); return; }
  if (status !== 202) { setupShown.start = { rejected: answer.error || String(status) }; renderSave(); return; }
  setupShown.start = { job: answer.job };
  renderSave();
  const job = await follow(name, answer.job.id, j => { setupShown.start = { job: j }; renderSave(); });
  body.startexit = String(job.exit_code);
  // A started session is shown in the experiment view.
  if (job.exit_code === 0) setTimeout(() => go(name, 'view'), 1500);
}

async function follow(session, id, update) {
  for (;;) {
    await new Promise(r => setTimeout(r, FOLLOW_MS));
    try {
      const answer = await (await fetch(`/api/jobs?session=${encodeURIComponent(session)}`, { cache: 'no-store' })).json();
      const job = (answer.jobs || []).find(j => j.id === id);
      if (job) { update(job); if (!job.running) return job; }
    } catch (err) { /* The next reading tries again. */ }
  }
}

function result(shown) {
  const { t, el } = ctx;
  if (!shown) return [];
  if (shown.pending) return [el('p', { class: 'op-status running' }, t('ctl_running') + ' \u2026'), ...(shown.command ? [el('code', { class: 'cmd ran' }, shown.command)] : [])];
  if (shown.message) return [el('p', { class: 'op-status bad', 'data-t': shown.message }, t(shown.message))];
  if (shown.invalid) return [el('p', { class: 'op-status bad', 'data-t': 'setup_invalid' }, t('setup_invalid')), el('pre', { class: 'op-output', id: 'save-problems' }, shown.invalid.join('\n'))];
  if (shown.rejected) return [el('p', { class: 'op-status bad' }, `${t('ctl_rejected')} ${shown.rejected}`)];
  if (shown.image) return [el('p', { class: 'op-status bad', 'data-t': 'image_missing' }, t('image_missing')), el('code', { class: 'cmd' }, shown.image.build)];
  if (shown.busy) return [el('p', { class: 'op-status bad', 'data-t': 'ctl_busy' }, t('ctl_busy')), ...(shown.job ? jobBlock(shown.job) : [])];
  if (shown.job) return jobBlock(shown.job);
  return [];
}

function renderSave() {
  if (!built) return;
  const { t, el } = ctx;
  const saveBox = document.getElementById('setup-save'), startBox = document.getElementById('session-start');
  if (!draft) {
    // Nothing to save or start until a setup file is chosen or made.
    saveBox.hidden = startBox.hidden = true;
    document.getElementById('save-button').disabled = document.getElementById('start-button').disabled = true;
    document.querySelector('.setup-actions .shared-command').hidden = true;
    return;
  }
  saveBox.hidden = startBox.hidden = false;
  document.querySelector('.setup-actions .shared-command').hidden = false;
  const nameField = document.getElementById('setup-name');
  if (document.activeElement !== nameField) nameField.value = draft.file;
  const valid = !!checkResult && checkResult.code === 0;
  document.getElementById('setup-state').textContent = t(draft.dirty ? 'setup_unsaved' : 'setup_saved_state');
  document.getElementById('setup-state').dataset.t = draft.dirty ? 'setup_unsaved' : 'setup_saved_state';
  document.getElementById('save-button').disabled = !canOperate() || !valid;
  const confirm = document.getElementById('save-confirm');
  confirm.hidden = confirmBox !== 'overwrite';
  if (confirmBox === 'overwrite') {
    const yes = el('button', { class: 'op danger', id: 'overwrite-yes', type: 'button', 'data-t': 'setup_replace' }, t('setup_replace'));
    const no = el('button', { class: 'op', id: 'overwrite-no', type: 'button', 'data-t': 'ctl_cancel' }, t('ctl_cancel'));
    yes.addEventListener('click', () => save(true));
    no.addEventListener('click', () => { confirmBox = null; renderSave(); });
    confirm.replaceChildren(el('p', { 'data-t': 'setup_exists_confirm' }, t('setup_exists_confirm')), el('code', { class: 'cmd' }, 'configs/setups/' + draft.file), el('div', {}, yes, ' ', no));
  }
  document.getElementById('save-result').replaceChildren(...result(setupShown.save));
  // Start: only from the saved, unchanged file.
  const select = document.getElementById('start-alignment');
  const options = (list ? list.alignments : []).join(',');
  if (select.dataset.options !== options) {
    const keep = select.value;
    select.replaceChildren(el('option', { value: '', 'data-t': 'start_no_alignment' }, t('start_no_alignment')), ...(list ? list.alignments : []).map(a => el('option', { value: a }, a)));
    select.value = (list && list.alignments.includes(keep)) ? keep : '';
    select.dataset.options = options;
  }
  const ready = !!draft.saved && !draft.dirty && valid;
  const imageNote = document.getElementById('start-image');
  imageNote.replaceChildren(...(list && !list.image ? [el('p', { class: 'alert warn' }, el('span', { 'data-t': 'image_missing' }, t('image_missing')), ' ', el('code', {}, 'scripts/build_session.sh'))] : []));
  document.getElementById('start-needs-save').hidden = ready;
  document.getElementById('start-button').disabled = !canOperate() || !ready || !(list && list.image) || !!(setupShown.start && (setupShown.start.pending || (setupShown.start.job && setupShown.start.job.running)));
  document.getElementById('start-command').textContent = startCommand();
  document.getElementById('save-command-source').textContent = cmd(['scripts/setup_session.sh', 'check', 'configs/setups/' + draft.file]);
  setupCommandChoice();
  document.getElementById('start-result').replaceChildren(...result(setupShown.start));
  body.setupdirty = draft.dirty ? 'yes' : 'no';
}

async function readList() {
  try { list = await (await fetch('/api/setups', { cache: 'no-store' })).json(); } catch (err) { return; }
  renderFiles();
  renderSave();
}

// ---- DAQ tab ----
async function readScreen() {
  screenTimer = null;
  if (!daqActive || document.hidden || !ctx.session) return;
  if (!screenBusy) {
    screenBusy = true;
    try {
      const answer = await (await fetch(`/api/screen?session=${encodeURIComponent(ctx.session)}&window=${windowName}`, { cache: 'no-store' })).json();
      if (answer.window === windowName) {
        const pre = document.getElementById('screen');
        pre.textContent = answer.text;
        pre.dataset.window = answer.window;
        pre.dataset.read = answer.read_utc;
        const note = document.getElementById('screen-read');
        note.replaceChildren(ctx.el('span', { 'data-t': 'daq_read_at', hidden:'' }, ctx.t('daq_read_at')), ctx.t('updated') + ' ' + answer.read_utc.slice(11, 19) + ' UTC · ' + ctx.t('read_interval').replace('{n}', '1'));
        if (!answer.ok) note.append(' \u00b7 ', ctx.el('span', { 'data-t': 'daq_unreadable' }, ctx.t('daq_unreadable')), ' ' + answer.error);
        body.screenreads = String(Number(body.screenreads || 0) + 1);
      }
    } catch (err) { /* The next reading tries again. */ }
    screenBusy = false;
  }
  if (daqActive && !document.hidden) screenTimer = setTimeout(readScreen, SCREEN_MS);
}

function daqLoop(on) {
  daqActive = on;
  clearTimeout(screenTimer);
  screenTimer = null;
  if (on) readScreen();
}

async function sendKey(key) {
  daqShown.keys = { pending: true, command: keyCommand(key) };
  daqConfirm = null;
  renderDaq();
  const action = key === 'R' ? 'repeat' : key === 'T' ? 'terminate' : 'key';
  const fields = key === 'Q' ? { session: ctx.session, key } : { session: ctx.session };
  const { status, answer } = await operate(action, fields);
  daqShown.keys = status === 200 ? { job: answer.job } : { rejected: answer.error || String(status) };
  renderDaq();
  readScreen();
  refreshLive();
}

async function copyOutput() {
  daqShown.end = { pending: true, command: copyCommand() };
  renderDaq();
  const { status, answer } = await operate('copy', { session: ctx.session });
  if (status === 409) { daqShown.end = { busy: true, job: answer.job }; renderDaq(); return; }
  if (status !== 202) { daqShown.end = { rejected: answer.error || String(status) }; renderDaq(); return; }
  daqShown.end = { job: answer.job };
  renderDaq();
  const job = await follow(ctx.session, answer.job.id, j => { daqShown.end = { job: j }; renderDaq(); });
  body.copyexit = String(job.exit_code);
}

async function removeContainer() {
  daqConfirm = null;
  daqShown.end = { pending: true, command: removeCommand() };
  renderDaq();
  const { status, answer } = await operate('remove', { session: ctx.session });
  daqShown.end = status === 200 ? { job: answer.job } : { rejected: answer.error || String(status) };
  renderDaq();
  if (status === 200 && answer.job.exit_code === 0) { body.removed = 'yes'; setTimeout(() => go(null, 'setup'), 1500); }
}

let daqConfirm = null;

function confirmation(text, command, yesText, onYes) {
  const { t, el } = ctx;
  const yes = el('button', { class: 'op danger', id: 'confirm-yes', type: 'button', 'data-t': yesText }, t(yesText));
  const no = el('button', { class: 'op', id: 'confirm-no', type: 'button', 'data-t': 'ctl_cancel' }, t('ctl_cancel'));
  yes.addEventListener('click', onYes);
  no.addEventListener('click', () => { daqConfirm = null; renderDaq(); });
  return el('div', { class: 'confirm', id: 'daq-confirm' }, el('p', { 'data-t': text }, t(text)), el('code', { class: 'cmd' }, command), el('div', {}, yes, ' ', no));
}

function buildDaq() {
  const { t, el } = ctx;
  const bar = document.getElementById('window-bar');
  bar.replaceChildren(...WINDOWS.map(w => {
    const b = el('button', { type: 'button', 'data-window': w, class: w === windowName ? 'active' : '' }, el('code', {}, w), ' ', el('span', { 'data-t': 'win_' + w }, t('win_' + w)));
    b.addEventListener('click', () => { windowName = w; for (const x of bar.querySelectorAll('button')) x.classList.toggle('active', x === b); document.getElementById('screen').textContent = ''; readScreen(); });
    return b;
  }));
  const keys = document.getElementById('key-controls');
  const entries = KEYS.map(k => {
    const b = el('button', { class:'op key', type:'button', 'data-key':k, 'data-t':'key_send' }, t('key_send'));
    b.setAttribute('aria-label', t('key_send') + ' ' + k);
    b.addEventListener('click', () => { if (CONFIRM_KEYS.includes(k)) { daqConfirm = 'key_' + k; renderDaq(); } else sendKey(k); });
    return { button:b, source:el('code', { 'data-keycommand':k, 'data-command-source':'' }, keyCommand(k)),
      row:el('div', { class:'key-line' }, el('kbd', {}, k), el('span', { 'data-t':'key_'+k.toLowerCase() }, t('key_'+k.toLowerCase())), b) };
  });
  const output = el('code', { class:'cmd', 'data-shared-command':'keys' }, '');
  keys.replaceChildren(el('h2', { 'data-t': 'keys_title' }, t('keys_title')), el('p', { class: 'note', 'data-t': 'keys_note' }, t('keys_note')),
    ...entries.map(e=>e.row), el('div', { hidden:'' }, ...entries.map(e=>e.source)), el('div', { class:'shared-command' }, output),
    el('div', { id: 'key-confirm-box' }), el('div', { class: 'hint' }), el('div', { class: 'op-result', id: 'key-result' }));
  keyCommandChoice = bindCommandChoice(entries, output);
  const end = document.getElementById('end-controls');
  const copy = el('button', { class: 'op', type: 'button', id: 'copy-button', 'data-t': 'copy_button' }, t('copy_button'));
  const remove = el('button', { class: 'op danger', type: 'button', id: 'remove-button', 'data-t': 'remove_button' }, t('remove_button'));
  copy.addEventListener('click', copyOutput);
  remove.addEventListener('click', () => { daqConfirm = 'remove'; renderDaq(); });
  end.replaceChildren(el('h2', { 'data-t': 'end_title' }, t('end_title')), el('p', { class: 'note', 'data-t': 'end_steps' }, t('end_steps')),
    el('div', { class:'end-step' }, '1. ', el('span', { 'data-t':'end_step_1' }, t('end_step_1'))),
    el('div', { class:'end-step' }, el('p', {}, '2. ', el('span', { 'data-t':'end_step_2' }, t('end_step_2'))),
      el('div', { class: 'op-row' }, copy, el('code', { class: 'cmd', id: 'copy-command' }, copyCommand())),
      el('p', { class: 'note' }, el('span', { 'data-t': 'copy_note' }, t('copy_note')), ' ', el('code', {}, 'outputs/'))),
    el('div', { class:'end-step' }, el('p', {}, '3. ', el('span', { 'data-t':'end_step_3' }, t('end_step_3'))),
      el('div', { class: 'op-row' }, remove, el('code', { class: 'cmd', id: 'remove-command' }, removeCommand()))),
    el('div', { id: 'end-confirm-box' }), el('div', { class: 'hint' }), el('div', { class: 'op-result', id: 'end-result' }));
}

function renderDaq() {
  if (!built) return;
  const { t, el } = ctx;
  const on = canOperate() && !!ctx.session;
  const running = sessionsList.some(s => s.name === ctx.session && s.state === 'running');
  for (const b of document.querySelectorAll('button[data-key]')) b.disabled = !on || !running;
  for (const b of document.querySelectorAll('button[data-key]')) b.setAttribute('aria-label', t('key_send') + ' ' + b.dataset.key);
  keyCommandChoice();
  const copyBusy = !!(daqShown.end && (daqShown.end.pending || (daqShown.end.job && daqShown.end.job.running)));
  document.getElementById('copy-button').disabled = !on || !running || copyBusy;
  document.getElementById('remove-button').disabled = !on || copyBusy || !sessionsList.some(s => s.name === ctx.session);
  const keyBox = document.getElementById('key-confirm-box'), endBox = document.getElementById('end-confirm-box');
  keyBox.replaceChildren(...(daqConfirm && daqConfirm.startsWith('key_') ? [confirmation('key_confirm_' + daqConfirm.slice(4).toLowerCase(), keyCommand(daqConfirm.slice(4)), 'key_send', () => sendKey(daqConfirm.slice(4)))] : []));
  endBox.replaceChildren(...(daqConfirm === 'remove' ? [confirmation('remove_confirm', removeCommand(), 'remove_button', removeContainer)] : []));
  document.getElementById('key-result').replaceChildren(...result(daqShown.keys));
  document.getElementById('end-result').replaceChildren(...result(daqShown.end));
  document.getElementById('view-finish-result')?.replaceChildren(...result(daqShown.end || daqShown.keys));
  document.getElementById('daq-none').hidden = !!ctx.session;
  document.getElementById('daq-body').hidden = !ctx.session;
  body.daqconfirm = daqConfirm || '';
}

// ---- language and tabs ----
export function sessionLanguage() {
  if (!ctx) return;
  renderSessions();
  renderEditor();
  renderSave();
  if (preview) preview.relabel();
  document.getElementById('preview-expand').setAttribute('aria-label', ctx.t('preview_expand'));
  document.getElementById('preview-close').setAttribute('aria-label', ctx.t('preview_close'));
  if (previewPlaceholder) document.getElementById('preview-dialog').setAttribute('aria-label', ctx.t('setup_preview'));
  renderDaq();
}

export function sessionTab(tab) {
  setupActive = tab === 'setup';
  daqLoop(tab === 'daq');
  if (setupActive && preview) preview.render(true);
}

export async function startSetup(options) {
  ctx = options;
  body.screenreads = '0';
  document.getElementById('session-pick').addEventListener('change', e => go(e.target.value || null, e.target.value ? (body.tab || 'view') : 'setup'));
  preview = new Scene3D(document.getElementById('setup-scene'), document.getElementById('setup-scene-labels'),
                        document.getElementById('setup-renderer'), { el: ctx.el, t: ctx.t });
  preview.create();
  document.getElementById('preview-expand').addEventListener('click', openPreview);
  document.getElementById('preview-close').addEventListener('click', closePreview);
  document.addEventListener('keydown', previewKeys);
  for (const b of document.querySelectorAll('#setup [data-view]')) b.addEventListener('click', () => preview.setView(b.dataset.view));
  document.getElementById('setup-new').addEventListener('click', newSetup);
  document.getElementById('save-button').addEventListener('click', () => save(false));
  document.getElementById('setup-name').addEventListener('input', e => { if (draft) { draft.file = e.target.value.trim(); if (draft.file !== draft.saved) draft.dirty = true; renderSave(); } });
  document.getElementById('start-button').addEventListener('click', runStart);
  document.getElementById('start-name').addEventListener('input', renderSave);
  document.getElementById('start-alignment').addEventListener('change', renderSave);
  setupCommandChoice = bindCommandChoice([
    {button:document.getElementById('save-button'),source:document.getElementById('save-command-source')},
    {button:document.getElementById('start-button'),source:document.getElementById('start-command')}
  ], document.getElementById('setup-shared-command'));
  document.addEventListener('visibilitychange', () => { if (!document.hidden && daqActive) readScreen(); });
  const screen = document.getElementById('screen');
  const fitTerminal = () => {
    const style = getComputedStyle(screen), canvas = document.createElement('canvas'), context = canvas.getContext('2d');
    context.font = '16px ' + style.fontFamily;
    const width = screen.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) - 4;
    const size = Math.max(11, Math.min(16, Math.floor(16 * width / context.measureText('M'.repeat(80)).width)));
    screen.style.setProperty('--terminal-size', size + 'px');
  };
  new ResizeObserver(fitTerminal).observe(screen); window.addEventListener('resize', fitTerminal);
  document.getElementById('check-command').textContent = cmd(['scripts/setup_session.sh', 'check']);
  document.getElementById('show-command').textContent = cmd(['scripts/setup_session.sh', 'show']);
  buildDaq();
  built = true;
  setFinishHandlers({ end: () => sendKey('T'), leave: () => sendKey('Q'), copy: copyOutput, remove: removeContainer });
  onControlChange(() => { if (draft) renderSave(); renderDaq(); });
  renderEditor();
  await readList();
  await readSessions();
  setInterval(readSessions, SESSIONS_MS);
  renderDaq();
}
