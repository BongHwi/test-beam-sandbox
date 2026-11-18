// SPDX-License-Identifier: LGPL-3.0-or-later
import { operate, canOperate, onControlChange } from './control.js';

let ctx, mode = 'eudaq', active = false, timer = null, busy = false, commandBusy = false;
let data = null, failed = false, screensRequested = false;
const node = id => document.getElementById(id);
const val = value => value === null || value === undefined || value === '' ? '-' : String(value);
const time = value => value ? value.slice(11, 19) + ' UTC' : '-';

function table(id, headings, rows) {
  const { el, t } = ctx;
  node(id).replaceChildren(el('thead', {}, el('tr', {}, ...headings.map(h => el('th', {}, t(h))))),
    el('tbody', {}, ...rows.map(row => el('tr', row.attrs || {}, ...row.values.map(v => el('td', {}, val(v)))))));
}
function render() {
  if (!ctx || !data) { buttons(); return; }
  const { t, el } = ctx, c = data.control || {}, devices = c.devices || [], sources = c.sources || [];
  const states = devices.map(d => d.state), all = states.length && states.every(s => s === states[0]);
  node('nd-state').textContent = !states.length ? 'NO DEVICES' : all ? states[0] : 'MIXED: ' + devices.map(d => `${d.service}: ${d.state}`).join(', ');
  node('nd-run-label').textContent = t(states.includes('RUNNING') ? 'nd_run' : 'nd_next_run');
  const run = val(states.includes('RUNNING') ? c.latest_run_number : (c.run_number ?? 1));
  node('nd-run').textContent = /^\d+$/.test(run) ? run : '-';
  node('nd-input').textContent = val(data.input);
  table('nd-devices', ['nd_device', 'nd_role', 'nd_state'], [...devices].sort((a,b) => a.service.localeCompare(b.service)).map(d => ({values:[d.service, d.service.startsWith('msensor-source-') ? t('nd_input') : d.service === 'FileSink' ? t('nd_sink') : d.service === 'MSensorDqm' ? 'DQM' : '-', d.state]})));
  const socket = data.input === 'socket';
  const headers = ['nd_msensor','nd_runner_state','nd_run','nd_frames_written','nd_last_frame','nd_frames_sent','nd_dqm_dropped','nd_reason'];
  if (socket) headers.push('nd_frames_missing','nd_reconnects','nd_dropped_frames');
  table('nd-streams', headers, (data.runners || []).map(r => {
    const s = sources.find(x => x.member.split(':')[0] === 'msensor-source-' + r.name) || {};
    const values = [r.name,r.state,r.run,r.frames_written,r.last_tick?.frame,s.frames_sent,s.dqm_dropped,s.reason];
    if (socket) values.push(s.frames_missing,r.socket?.reconnects,r.socket?.dropped_frames);
    return {values};
  }));
  const health = [], drain = c.drain_last;
  if (drain && !drain.complete) health.push(`${t('nd_drain')}: ${(drain.missing || []).map(x => typeof x === 'string' ? x : x.member).join(', ')} ${(drain.not_running || []).map(x => `${x.member}: ${x.state}`).join(', ')}`);
  for (const s of sources) if (s.reason) health.push(`${s.member}: ${val(s.state)}: ${s.reason}`);
  for (const r of data.runners || []) if (r.error || r.last_error != null) health.push(`${r.name}: ${r.error || r.last_error}`);
  node('nd-health').replaceChildren(...(health.length ? health : ['OK']).map(line => el('p', {}, line)));
  table('nd-files', ['nd_name','nd_size','nd_time'], [...(data.files || [])].sort((a,b) => (b.mtime_utc || '').localeCompare(a.mtime_utc || '')).map(f => ({attrs:{'data-name':f.name,'data-bytes':String(f.bytes)},values:[f.name,f.bytes,time(f.mtime_utc)]})));
  node('nd-read').textContent = time(data.read_utc);
  buttons();
}
function buttons() {
  if (!ctx) return;
  const states = (data?.control?.devices || []).map(d => d.state), usable = canOperate() && !commandBusy && !failed;
  node('nd-start').disabled = !(usable && states.length && (states.every(s => s === 'IDLE') || states.every(s => s === 'READY')));
  node('nd-stop').disabled = !(usable && states.includes('RUNNING') && states.every(s => s === 'RUNNING' || s === 'READY'));
}
async function send(action) {
  commandBusy = true; buttons();
  const { status, answer } = await operate(action, {session:ctx.session});
  node('nd-run-alert').textContent = status === 200 ? (answer.job?.exit_code === 0 ? '' : (answer.job?.output || '').split('\n')[0]) : (answer.error || String(status));
  node('nd-run-alert').hidden = !node('nd-run-alert').textContent;
  commandBusy = false; buttons();
  read();
}
async function screens() {
  if (screensRequested || !canOperate()) return;
  screensRequested = true;
  const {status, answer} = await operate('screens', {session:ctx.session});
  const box = node('nd-screens'); box.replaceChildren();
  if (status === 200) for (const name of ['webctl','slowdash','dqm']) {
    const url = answer.screens?.[name]?.url;
    if (typeof url === 'string' && url.startsWith('http://127.0.0.1:')) box.append(ctx.el('div', {class:'nd-screen-card'}, ctx.el('a', {'data-nd-screen':name, href:url, target:'_blank', rel:'noopener noreferrer'}, {webctl:'daq-webctl',slowdash:'SlowDash',dqm:'DQM'}[name]), ctx.el('p', {'data-t':'nd_screen_' + name}, ctx.t('nd_screen_' + name))));
  }
  if (!box.childNodes.length) box.textContent = ctx.t('nd_screens_unavailable');
}
async function read() {
  clearTimeout(timer); timer = null;
  if (mode !== 'nestdaq' || !active || document.hidden || busy) return;
  busy = true;
  try {
    const response = await fetch(`/api/nestdaq?session=${encodeURIComponent(ctx.session)}`, {cache:'no-store'});
    const answer = await response.json();
    if (!response.ok) { failed = true; node('nd-alert').textContent = answer.error || String(response.status); }
    else { failed = false; node('nd-alert').textContent = ''; data = answer; render(); }
  } catch (err) { failed = true; node('nd-alert').textContent = String(err); }
  node('nd-alert').hidden = !node('nd-alert').textContent;
  busy = false; buttons();
  if (mode === 'nestdaq' && active && !document.hidden) timer = setTimeout(read, 1000);
}
export function setMode(value) {
  mode = value;
  document.querySelector('button[data-tab="nestdaq"]').hidden = value !== 'nestdaq';
  for (const p of document.querySelectorAll('.nestdaq-note')) p.hidden = value !== 'nestdaq';
  if (active && value === 'nestdaq') { screens(); read(); }
}
export function nestdaqTab(tab) { active = tab === 'nestdaq'; clearTimeout(timer); if (active && mode === 'nestdaq') { screens(); read(); } }
export function nestdaqLanguage() { if (ctx) { render(); if (!node('nd-screens').querySelector('a')) node('nd-screens').textContent = ctx.t('nd_screens_unavailable'); } }
export function startNestdaq(options) {
  ctx = options;
  const {el,t,session} = ctx, section = node('nestdaq');
  const title = (tag,key) => el(tag, {'data-t':key}, t(key));
  const line = (key,id) => el('p', {}, el('span', {'data-t':key}, t(key)), ': ', el('span', {id}, '-'));
  const start = el('button', {class:'op',id:'nd-start',type:'button','data-t':'nd_start'}, t('nd_start'));
  const stop = el('button', {class:'op',id:'nd-stop',type:'button','data-t':'nd_stop'}, t('nd_stop'));
  start.addEventListener('click', () => send('nestdaq_start')); stop.addEventListener('click', () => send('nestdaq_stop'));
  const card = (name, ...children) => el('div', {class:'nd-card', 'data-nd-card':name}, ...children);
  section.append(el('div', {class:'nd-layout'},
    el('div', {class:'nd-column'},
      card('run', title('h2','nd_run'), line('nd_state','nd-state'), el('p', {}, el('span',{id:'nd-run-label'},t('nd_next_run')), ' ', el('span',{id:'nd-run'},'-')), line('nd_input','nd-input'), start, ' ', stop, el('div',{class:'hint'}), el('p',{class:'alert warn',id:'nd-run-alert',hidden:''}), el('div',{},el('code',{class:'cmd'},`scripts/session_control.sh ${session || 'NAME'} nestdaq start`)), el('div',{},el('code',{class:'cmd'},`scripts/session_control.sh ${session || 'NAME'} nestdaq stop`)), el('p',{class:'alert warn',id:'nd-alert',hidden:''})),
      card('devices', title('h2','nd_devices'), el('div',{class:'wide'},el('table',{id:'nd-devices'}))),
      card('streams', title('h2','nd_streams'), el('div',{class:'wide'},el('table',{id:'nd-streams'})))),
    el('div', {class:'nd-column'},
      card('screens', title('h2','nd_screens'), el('p',{class:'note','data-t':'nd_screens_note'},t('nd_screens_note')), el('div',{id:'nd-screens'},t('nd_screens_unavailable'))),
      card('health', title('h2','nd_health'), el('div',{id:'nd-health'})),
      card('files', title('h2','nd_files'), el('div',{class:'wide'},el('table',{id:'nd-files'})), el('p',{class:'note',id:'nd-read'})))));
  onControlChange(() => { buttons(); if (active && mode === 'nestdaq') screens(); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden && active) read(); });
  buttons();
}
