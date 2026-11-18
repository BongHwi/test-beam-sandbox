// SPDX-License-Identifier: LGPL-3.0-or-later
// Page operations. Each button runs one existing command through the page server, and the page shows
// that command line, its output and its exit code. Requests carry the token of the page address.

const SCRIPTS = { beam_on: ['scripts/session_control.sh', 'on'], beam_off: ['scripts/session_control.sh', 'off'],
                  stop: ['scripts/session_control.sh', 'stop'], align: ['scripts/align_session.sh'],
                  qa: ['scripts/analyze_session.sh'] };
const LONG = ['align', 'qa'];
// Background operations: one of them at a time per session.
const BACKGROUND = ['align', 'qa', 'start_session', 'copy'];
const LABELS = { beam_on: 'ctl_beam_on', beam_off: 'ctl_beam_off', stop: 'ctl_stop', end: 'ctl_end', leave: 'ctl_leave', copy: 'copy_button', remove: 'remove_button', align: 'ctl_align', qa: 'ctl_qa' };
const PAGE_IDS = { beam_on: 'page-on', beam_off: 'page-off', stop: 'page-stop' };
const ICONS = { beam_on:'M12 3v9 M6.3 7.3a8 8 0 1 0 11.4 0', beam_off:'M12 3v9 M6.3 7.3a8 8 0 1 0 11.4 0',
  stop:'M6 6h12v12H6z', align:'M12 3v18 M3 12h18 M7 7l10 10 M17 7 7 17', qa:'M4 19h16 M6 16l4-5 3 2 5-7' };

function addIcon(button) {
  if (button.querySelector('svg') || !ICONS[button.dataset.action]) return;
  const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg'), path = document.createElementNS(ns, 'path');
  for (const [name,value] of Object.entries({width:'16',height:'16',viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':'1.5','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'})) svg.setAttribute(name,value);
  path.setAttribute('d', ICONS[button.dataset.action]); svg.append(path); button.prepend(svg);
}

let ctx = null, token = null, viewOnly = false, live = null, state = null, jobs = [], log = [];
let mode = 'eudaq';
export function setMode(value) { mode = value; render(); }
let shown = {}, timer = null, confirmStop = null, finishHandlers = {};
const body = document.body.dataset;

function readToken() {
  // The token arrives after '#' (never sent to the server); it moves to this tab's storage and leaves the address.
  const key = 'page-token-' + location.port;
  const hash = new URLSearchParams(location.hash.slice(1));
  let value = hash.get('token');
  if (value) {
    try { sessionStorage.setItem(key, value); } catch (err) { /* The token still works until the page is closed. */ }
    history.replaceState(null, '', location.pathname + location.search);
  } else {
    try { value = sessionStorage.getItem(key); } catch (err) { value = null; }
  }
  return value || null;
}

export function commandLine(action, run) {
  const name = ctx.session || 'NAME';
  if (action === 'end') return ['scripts/session_control.sh', name, 'terminate', '--id', 'page-terminate'].join(' ');
  if (action === 'leave') return ['docker', 'exec', name, 'tmux', 'send-keys', '-t', 'ITS3:rc', 'Q'].join(' ');
  if (action === 'copy') return ['docker', 'cp', name + ':/tmp/session-run', 'outputs/' + name + '-UTCTIME'].join(' ');
  if (action === 'remove') return ['docker', 'rm', '-f', name].join(' ');
  const [script, ...rest] = SCRIPTS[action];
  return [script, ctx.session || 'NAME', ...rest, ...(PAGE_IDS[action] ? ['--id', PAGE_IDS[action]] : []),
          ...(LONG.includes(action) && run ? ['--run', run] : [])].join(' ');
}

const runningJob = () => jobs.find(j => j.running && BACKGROUND.includes(j.action));

// An operation request with the page token: { status, answer }.
export async function operate(action, fields) {
  try {
    const response = await fetch('/api/control', { method: 'POST', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', 'X-Page-Token': token || '' },
      body: JSON.stringify({ action, ...fields }) });
    return { status: response.status, answer: await response.json().catch(() => ({})) };
  } catch (err) {
    return { status: 0, answer: { error: String(err) } };
  }
}

export const canOperate = () => !!token && !viewOnly;
export const currentJobs = () => jobs;
export const backgroundJob = () => runningJob();
export function setFinishHandlers(handlers) { finishHandlers = handlers; render(); }

// One visible command follows the focused or hovered enabled button.
export function bindCommandChoice(entries, output) {
  let chosen = null;
  const usable = entry => !entry.button.disabled && !entry.button.hidden;
  const sync = () => {
    if (!chosen || !usable(chosen)) chosen = entries.find(usable) || entries.find(e => !e.button.hidden) || entries[0];
    output.textContent = chosen ? chosen.source.textContent : '';
  };
  for (const entry of entries) for (const event of ['focus', 'mouseenter']) entry.button.addEventListener(event, () => {
    if (usable(entry)) { chosen = entry; sync(); }
  });
  return sync;
}

function allowed(action) {
  if (!token || viewOnly) return false;
  if (mode === 'nestdaq') {
    if (action === 'copy' || action === 'remove') return !!(live && live.running && !(live.nestdaq && live.nestdaq.running) && !runningJob());
    if (action === 'beam_on') return !!(live && live.running && live.beam_on === false);
    if (action === 'beam_off') return !!(live && live.running && live.beam_on === true);
    if (['stop', 'end', 'leave'].includes(action)) return false;
  }
  const running = !!(live && live.running && live.rc_alive !== false && live.rc_state === 'RUNNING');
  if (action === 'beam_on') return running && live.beam_on === false;
  if (action === 'beam_off') return running && live.beam_on === true;
  if (action === 'stop' || action === 'end') return running;
  if (action === 'leave') return !!(live && live.running && live.rc_state === 'TERMINATED' && live.rc_alive === true);
  if (action === 'copy' || action === 'remove') return !!(live && live.running && live.rc_alive === false && !runningJob());
  return !!(state && state.running) && !runningJob();
}

function selectedRun() {
  const select = document.getElementById('ctl-run');
  return select ? select.value : '';
}

function button(action) {
  const { el, t } = ctx;
  const b = el('button', { class: 'op' + (['stop','end','leave','remove'].includes(action) ? ' danger' : action === 'qa' ? ' primary' : ''), 'data-action': action, 'data-t': LABELS[action] }, t(LABELS[action]));
  b.addEventListener('click', () => {
    if (['stop','end','leave','remove'].includes(action)) { confirmStop = action; render(); }
    else if (action === 'copy') finishHandlers.copy?.();
    else send(action);
  });
  return { button:b, source:el('code', { 'data-command':action, 'data-command-source':'' }, commandLine(action, '')) };
}

const commandChoices = {};
function build() {
  const { el, t } = ctx;
  for (const [id, actions] of [['view-controls', ['beam_on', 'beam_off', 'stop', 'end', 'leave', 'copy', 'remove']], ['qa-controls', ['align', 'qa']]]) {
    const box = document.getElementById(id);
    const parts = [el('h2', { 'data-t': 'controls', ...(id === 'qa-controls' ? { hidden:'' } : {}) }, t('controls'))];
    if (id === 'qa-controls') {
      parts.push(el('label', { class: 'op-run' }, el('span', { 'data-t': 'run' }, t('run')), ' ',
                    el('select', { id: 'ctl-run' }, el('option', { value: '', 'data-t': 'ctl_current_run' }, t('ctl_current_run')))));
    }
    const entries = actions.map(button), output = el('code', { class:'cmd', 'data-shared-command':id }, '');
    parts.push(el('div', { class:'action-buttons' }, ...entries.map(e=>e.button)),
      el('div', { hidden:'' }, ...entries.map(e=>e.source)), el('div', { class:'shared-command' }, output));
    commandChoices[id] = bindCommandChoice(entries, output);
    if (id === 'view-controls') parts.push(el('div', { id: 'stop-confirm', class: 'confirm', hidden: '' }), el('div', { id:'view-finish-result' }));
    parts.push(el('div', { class: 'hint' }), el('div', { class: 'op-result', id: id + '-result' }),
               el('details', { class: 'op-log' }, el('summary', { 'data-t': 'ctl_log' }, t('ctl_log')), el('table', {})));
    box.replaceChildren(...parts);
  }
  document.getElementById('ctl-run').addEventListener('change', render);
}

async function send(action) {
  const run = LONG.includes(action) ? selectedRun() : '';
  const where = LONG.includes(action) ? 'qa-controls-result' : 'view-controls-result';
  confirmStop = null;
  shown[where] = { pending: true, action, command: commandLine(action, run) };
  render();
  const { status, answer } = await operate(action, { session: ctx.session, run });
  if (status === 409) shown[where] = { busy: true, job: answer.job };
  else if (status < 200 || status > 299) shown[where] = { rejected: answer.error || String(status), action };
  else shown[where] = { job: answer.job };
  await pollJobs();
  if (!LONG.includes(action)) ctx.refreshLive();
}

export function jobBlock(job) {
  const { el, t } = ctx;
  const elapsed = Math.max(0, Math.round(((job.finished_utc ? Date.parse(job.finished_utc) : Date.now()) - Date.parse(job.started_utc)) / 1000));
  const status = job.running ? `${t('ctl_running')} \u2026 ${elapsed} s ${t('ctl_elapsed')}`
    : `${t(job.exit_code === 0 ? 'ctl_ok' : 'ctl_failed')} \u00b7 ${t('ctl_exit')} ${job.exit_code}`;
  return [el('p', { class: 'op-status ' + (job.running ? 'running' : (job.exit_code === 0 ? 'ok' : 'bad')) }, status),
          el('code', { class: 'cmd ran' }, job.command), el('pre', { class: 'op-output' }, job.output || '')];
}

function render() {
  if (!ctx) return;
  const { el, t } = ctx;
  for (const b of document.querySelectorAll('button.op[data-action]')) { b.disabled = !allowed(b.dataset.action); addIcon(b); }
  for (const b of document.querySelectorAll('#view-controls button.op[data-action]')) {
    const held = canOperate() && mode === 'nestdaq' && ['copy','remove'].includes(b.dataset.action) && live && live.nestdaq && live.nestdaq.running;
    b.hidden = !allowed(b.dataset.action) && !held;
    b.disabled = !allowed(b.dataset.action);
    b.title = held ? t('nd_copy_after_stop') : '';
  }
  const run = selectedRun();
  for (const c of document.querySelectorAll('code[data-command]')) c.textContent = commandLine(c.dataset.command, run);
  Object.values(commandChoices).forEach(sync => sync());
  // The run choices of the QA tab: the session's raw files, newest first.
  const select = document.getElementById('ctl-run');
  const runs = (state && state.runs) || [];
  if (select && select.dataset.runs !== runs.join(',')) {
    const value = select.value;
    select.replaceChildren(el('option', { value: '', 'data-t': 'ctl_current_run' }, t('ctl_current_run')),
                           ...runs.map(r => el('option', { value: String(r) }, String(r))));
    select.value = runs.map(String).includes(value) ? value : '';
    select.dataset.runs = runs.join(',');
  }
  const confirm = document.getElementById('stop-confirm');
  if (confirm) {
    confirm.hidden = !confirmStop;
    if (confirmStop) {
      const action = confirmStop;
      const yes = el('button', { class: 'op danger', id: action === 'stop' ? 'stop-yes' : 'finish-yes' }, t(LABELS[action]));
      const no = el('button', { class: 'op', id: 'stop-no' }, t('ctl_cancel'));
      yes.addEventListener('click', () => {
        confirmStop = null; render();
        if (action === 'stop') send('stop'); else finishHandlers[action]?.();
      });
      no.addEventListener('click', () => { confirmStop = null; render(); });
      const message = {stop:'ctl_confirm_stop',end:'ctl_confirm_end',leave:'key_confirm_q',remove:'remove_confirm'}[action];
      confirm.replaceChildren(el('p', {}, t(message)), el('code', { class: 'cmd' }, commandLine(action, '')), el('div', {}, yes, ' ', no));
    }
  }
  for (const hint of document.querySelectorAll('.controls .hint')) {
    if (viewOnly) hint.replaceChildren(el('p', { class: 'note' }, t('ctl_server_view_only')));
    else if (!token) hint.replaceChildren(el('p', { class: 'note' }, t('ctl_view_only_hint') + ' '), el('code', {}, ['scripts/qa_page.sh', ctx.session || ''].join(' ').trim()));
    else hint.replaceChildren();
  }
  for (const where of ['view-controls-result', 'qa-controls-result']) {
    const box = document.getElementById(where);
    if (!box) continue;
    const s = shown[where];
    let parts = [];
    if (s && s.pending) parts = [el('p', { class: 'op-status running' }, t('ctl_running') + ' \u2026'), el('code', { class: 'cmd ran' }, s.command)];
    else if (s && s.busy) parts = [el('p', { class: 'op-status bad' }, t('ctl_busy')), ...(s.job ? jobBlock(s.job) : [])];
    else if (s && s.rejected) parts = [el('p', { class: 'op-status bad' }, `${t('ctl_rejected')} ${s.rejected}`)];
    else if (s && s.job) parts = jobBlock(jobs.find(j => j.id === s.job.id) || s.job);
    box.replaceChildren(...parts);
    const job = s && s.job ? (jobs.find(j => j.id === s.job.id) || s.job) : null;
    box.dataset.state = s && s.pending ? 'pending' : (job ? (job.running ? 'running' : 'done') : (s ? 'refused' : ''));
    box.dataset.exit = job && !job.running ? String(job.exit_code) : '';
    box.dataset.command = job ? job.command : '';
  }
  for (const table of document.querySelectorAll('.op-log table')) {
    table.replaceChildren(el('tr', {}, el('th', {}, t('ctl_time')), el('th', {}, t('ctl_action')), el('th', {}, t('ctl_exit'))),
      ...log.map(r => el('tr', {}, el('td', {}, (r.started_utc || '').slice(0, 19).replace('T', ' ')), el('td', {}, el('code', {}, r.command)),
                          el('td', {}, String(r.exit_code)))));
  }
  body.controls = token && !viewOnly ? 'on' : 'off';
  for (const f of listeners) f();
}

const listeners = [];
export function onControlChange(f) { listeners.push(f); }

export async function pollJobs() {
  if (!ctx.session) {
    // Without a session there are no session operations; the server still says whether it is view only.
    try { viewOnly = (await (await fetch('/api/sessions', { cache: 'no-store' })).json()).view_only; } catch (err) { /* later */ }
    render();
    clearTimeout(timer);
    timer = setTimeout(pollJobs, 10000);
    return;
  }
  try {
    const answer = await (await fetch(`/api/jobs?session=${encodeURIComponent(ctx.session)}`, { cache: 'no-store' })).json();
    const wasRunning = !!runningJob();
    viewOnly = answer.view_only;
    jobs = answer.jobs || [];
    log = answer.log || [];
    // A finished QA brings the new result to the QA tab.
    if (wasRunning && !runningJob()) ctx.refreshQa();
  } catch (err) { /* The next reading tries again. */ }
  render();
  clearTimeout(timer);
  timer = setTimeout(pollJobs, runningJob() || Object.values(shown).some(s => s && s.pending) ? 1000 : 10000);
}

export function controlLive(value) { live = value; render(); }
export function controlState(value) { state = value; render(); }
export function controlLanguage() { render(); }

export function startControl(options) {
  ctx = options;
  token = readToken();
  build();
  pollJobs();
}
