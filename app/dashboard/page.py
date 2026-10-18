"""Dashboard HTML (kept in Python so it ships inside the serverless bundle).

A single static page; all data comes from the public REST API via ``fetch``.
"""

from __future__ import annotations

import html

_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__CLINIC__ · Patient Registration</title>
<style>
  :root {
    --bg: #f4f6fa; --panel: #ffffff; --text: #16202c; --muted: #5d6b7c; --line: #dde3ec;
    --accent: #0f6fde; --accent-soft: #e6f0fd; --ok: #1b8a4c; --warn: #b7791f; --danger: #c0392b;
    --radius: 12px; --shadow: 0 1px 2px rgba(16,24,40,.06), 0 1px 3px rgba(16,24,40,.1);
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --bg: #0f141b; --panel: #171e27; --text: #e8edf3; --muted: #98a4b3; --line: #273140;
      --accent: #5aa2ff; --accent-soft: #15283f; --shadow: none;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text); font: 15px/1.5 system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
  header { background: var(--panel); border-bottom: 1px solid var(--line); padding: 16px; }
  .wrap { max-width: 1180px; margin: 0 auto; padding: 0 16px; }
  header .wrap { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; padding: 0; }
  h1 { font-size: 18px; margin: 0; }
  h1 small { display: block; font-size: 13px; color: var(--muted); font-weight: 400; }
  .links a { color: var(--accent); text-decoration: none; margin-left: 14px; font-size: 14px; }
  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 20px 0; }
  .stat { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 14px 16px; box-shadow: var(--shadow); }
  .stat .n { font-size: 26px; font-weight: 600; }
  .stat .l { color: var(--muted); font-size: 13px; }
  .tabs { display: flex; gap: 6px; margin-bottom: 12px; flex-wrap: wrap; }
  .tabs button { background: transparent; border: 1px solid var(--line); color: var(--text); padding: 8px 14px; border-radius: 999px; cursor: pointer; font: inherit; }
  .tabs button.active { background: var(--accent-soft); border-color: var(--accent); color: var(--accent); font-weight: 600; }
  .toolbar { display: flex; gap: 10px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }
  input[type=search] { flex: 1; min-width: 220px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); color: var(--text); font: inherit; }
  button.primary { background: var(--accent); color: #fff; border: 0; padding: 10px 14px; border-radius: 8px; cursor: pointer; font: inherit; }
  .panel { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow); overflow: hidden; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }
  th { color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
  tbody tr { cursor: pointer; }
  tbody tr:hover { background: var(--accent-soft); }
  .empty { padding: 28px; color: var(--muted); text-align: center; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; background: var(--accent-soft); color: var(--accent); }
  .badge.ok { background: #e3f5ea; color: var(--ok); } .badge.warn { background: #fdf3df; color: var(--warn); } .badge.danger { background: #fde9e7; color: var(--danger); }
  @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .badge.ok { background: #143122; } :root:not([data-theme="light"]) .badge.warn { background: #3a2c10; } :root:not([data-theme="light"]) .badge.danger { background: #3b1a16; } }
  .layout { display: grid; grid-template-columns: 1fr; gap: 16px; }
  @media (min-width: 900px) { .layout.with-detail { grid-template-columns: 1.4fr 1fr; } }
  .detail { padding: 16px; }
  .detail h2 { margin: 0 0 4px; font-size: 18px; }
  .detail .sub { color: var(--muted); font-size: 13px; margin-bottom: 12px; }
  dl { display: grid; grid-template-columns: 160px 1fr; gap: 6px 12px; margin: 0; font-size: 14px; }
  dt { color: var(--muted); } dd { margin: 0; word-break: break-word; }
  .section { margin-top: 18px; padding-top: 12px; border-top: 1px solid var(--line); }
  .section h3 { font-size: 13px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); margin: 0 0 8px; }
  pre { white-space: pre-wrap; font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; background: var(--bg); padding: 10px; border-radius: 8px; max-height: 320px; overflow: auto; }
  .muted { color: var(--muted); }
  .close { float: right; background: transparent; border: 0; color: var(--muted); cursor: pointer; font-size: 18px; }
  footer { color: var(--muted); font-size: 12px; padding: 24px 0; }
  code { background: var(--bg); padding: 1px 5px; border-radius: 4px; }
</style>
</head>
<body>
<header><div class="wrap">
  <h1>__CLINIC__ <small>Patient registration dashboard · fed by the voice agent</small></h1>
  <div class="links"><a href="/docs">API docs</a><a href="/patients">GET /patients</a><a href="/vapi/assistant-config">Agent prompt</a></div>
</div></header>

<main class="wrap">
  <div class="stats">
    <div class="stat"><div class="n" id="stat-patients">–</div><div class="l">Registered patients</div></div>
    <div class="stat"><div class="n" id="stat-calls">–</div><div class="l">Calls handled</div></div>
    <div class="stat"><div class="n" id="stat-appts">–</div><div class="l">Appointments booked</div></div>
    <div class="stat"><div class="n" id="stat-updated">–</div><div class="l">Last refresh</div></div>
  </div>

  <div class="tabs">
    <button class="active" data-tab="patients">Patients</button>
    <button data-tab="calls">Calls &amp; transcripts</button>
    <button data-tab="appointments">Appointments</button>
  </div>

  <div class="toolbar">
    <input type="search" id="search" placeholder="Search by name, phone, date of birth, city…">
    <button class="primary" id="refresh">Refresh</button>
  </div>

  <div class="layout" id="layout">
    <div class="panel" id="list"></div>
    <div class="panel detail" id="detail" hidden></div>
  </div>
  <footer>Data is fetched live from the REST API (<code>/patients</code>, <code>/calls</code>, <code>/appointments</code>). Demo records only — no real patient data.</footer>
</main>

<script>
(function () {
  const state = { tab: 'patients', patients: [], calls: [], appointments: [], selected: null, q: '' };
  const $ = (s) => document.querySelector(s);
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const safeUrl = (u) => (/^https?:\/\//i.test(String(u || '')) ? u : '');  // never render javascript:/data: links
  const fmtPhone = (p) => p && p.length === 10 ? `(${p.slice(0,3)}) ${p.slice(3,6)}-${p.slice(6)}` : (p || '—');
  const fmtDate = (iso) => iso ? new Date(iso).toLocaleString() : '—';
  const fmtDob = (d) => d ? new Date(d + 'T00:00:00').toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—';

  async function getJson(url) {
    const r = await fetch(url, { headers: { Accept: 'application/json' } });
    const body = await r.json();
    if (!r.ok || body.error) throw new Error(body.error ? body.error.message : r.statusText);
    return body.data;
  }

  async function load() {
    try {
      const [patients, calls, appointments] = await Promise.all([
        getJson('/patients?limit=200'), getJson('/calls?limit=100'), getJson('/appointments'),
      ]);
      Object.assign(state, { patients, calls, appointments });
      $('#stat-patients').textContent = patients.length;
      $('#stat-calls').textContent = calls.length;
      $('#stat-appts').textContent = appointments.length;
      $('#stat-updated').textContent = new Date().toLocaleTimeString();
      render();
    } catch (err) {
      $('#list').innerHTML = `<div class="empty">Could not load data: ${esc(err.message)}</div>`;
    }
  }

  function matches(p, q) {
    if (!q) return true;
    const hay = [p.first_name, p.last_name, p.phone_number, p.date_of_birth, p.city, p.state, p.email, p.patient_id].join(' ').toLowerCase();
    return hay.includes(q);
  }

  function render() {
    const q = state.q.trim().toLowerCase();
    const list = $('#list');
    if (state.tab === 'patients') {
      const rows = state.patients.filter((p) => matches(p, q));
      list.innerHTML = rows.length ? `<table><thead><tr><th>Name</th><th>Date of birth</th><th>Sex</th><th>Phone</th><th>City / State</th><th>Language</th><th>Registered</th></tr></thead><tbody>${rows.map((p) => `
        <tr data-id="${esc(p.patient_id)}"><td><strong>${esc(p.first_name)} ${esc(p.last_name)}</strong></td><td>${fmtDob(p.date_of_birth)}</td><td>${esc(p.sex)}</td><td>${fmtPhone(p.phone_number)}</td><td>${esc(p.city)}, ${esc(p.state)}</td><td>${esc(p.preferred_language)}</td><td class="muted">${fmtDate(p.created_at)}</td></tr>`).join('')}</tbody></table>`
        : `<div class="empty">No patients yet. Call the agent's phone number to register one.</div>`;
      list.querySelectorAll('tbody tr').forEach((tr) => tr.addEventListener('click', () => select('patient', tr.dataset.id)));
    } else if (state.tab === 'calls') {
      const rows = state.calls.filter((c) => !q || [c.call_id, c.caller_number, c.summary, c.ended_reason].join(' ').toLowerCase().includes(q));
      list.innerHTML = rows.length ? `<table><thead><tr><th>When</th><th>Caller</th><th>Patient</th><th>Status</th><th>Duration</th><th>Summary</th></tr></thead><tbody>${rows.map((c) => {
        const p = state.patients.find((x) => x.patient_id === c.patient_id);
        return `<tr data-id="${esc(c.call_id)}"><td class="muted">${fmtDate(c.started_at || c.created_at)}</td><td>${esc(c.caller_number || '—')}</td><td>${p ? esc(p.first_name + ' ' + p.last_name) : '<span class="muted">not linked</span>'}</td><td><span class="badge ${c.status === 'ended' ? 'ok' : 'warn'}">${esc(c.status)}</span></td><td>${c.duration_seconds ? Math.round(c.duration_seconds) + 's' : '—'}</td><td>${esc(c.summary || '—')}</td></tr>`; }).join('')}</tbody></table>`
        : `<div class="empty">No calls recorded yet.</div>`;
      list.querySelectorAll('tbody tr').forEach((tr) => tr.addEventListener('click', () => select('call', tr.dataset.id)));
    } else {
      const rows = state.appointments;
      list.innerHTML = rows.length ? `<table><thead><tr><th>Scheduled for</th><th>Patient</th><th>Provider</th><th>Reason</th><th>Status</th></tr></thead><tbody>${rows.map((a) => {
        const p = state.patients.find((x) => x.patient_id === a.patient_id);
        return `<tr data-id="${esc(a.patient_id)}"><td>${fmtDate(a.scheduled_for)}</td><td>${p ? esc(p.first_name + ' ' + p.last_name) : esc(a.patient_id)}</td><td>${esc(a.provider_name)}</td><td>${esc(a.reason || '—')}</td><td><span class="badge ok">${esc(a.status)}</span></td></tr>`; }).join('')}</tbody></table>`
        : `<div class="empty">No appointments booked yet.</div>`;
      list.querySelectorAll('tbody tr').forEach((tr) => tr.addEventListener('click', () => select('patient', tr.dataset.id)));
    }
    renderDetail();
  }

  function select(kind, id) { state.selected = { kind, id }; renderDetail(); }

  function renderDetail() {
    const box = $('#detail');
    const layout = $('#layout');
    if (!state.selected) { box.hidden = true; layout.classList.remove('with-detail'); return; }
    box.hidden = false; layout.classList.add('with-detail');
    const closeBtn = `<button class="close" title="Close" id="close">×</button>`;
    if (state.selected.kind === 'patient') {
      const p = state.patients.find((x) => x.patient_id === state.selected.id);
      if (!p) { box.hidden = true; return; }
      const calls = state.calls.filter((c) => c.patient_id === p.patient_id);
      const appts = state.appointments.filter((a) => a.patient_id === p.patient_id);
      const row = (k, v) => `<dt>${esc(k)}</dt><dd>${v == null || v === '' ? '<span class="muted">—</span>' : esc(v)}</dd>`;
      box.innerHTML = `${closeBtn}<h2>${esc(p.first_name)} ${esc(p.last_name)}</h2><div class="sub">patient_id <code>${esc(p.patient_id)}</code></div>
        <dl>${row('Date of birth', fmtDob(p.date_of_birth))}${row('Sex', p.sex)}${row('Phone', fmtPhone(p.phone_number))}${row('Email', p.email)}
        ${row('Address', [p.address_line_1, p.address_line_2].filter(Boolean).join(', '))}${row('City / State / ZIP', `${p.city}, ${p.state} ${p.zip_code}`)}
        ${row('Insurance', p.insurance_provider)}${row('Member ID', p.insurance_member_id)}${row('Preferred language', p.preferred_language)}
        ${row('Emergency contact', p.emergency_contact_name)}${row('Emergency phone', fmtPhone(p.emergency_contact_phone))}
        ${row('Created (UTC)', p.created_at)}${row('Updated (UTC)', p.updated_at)}</dl>
        <div class="section"><h3>Appointments</h3>${appts.length ? appts.map((a) => `<div>${fmtDate(a.scheduled_for)} · ${esc(a.provider_name)} <span class="badge ok">${esc(a.status)}</span></div>`).join('') : '<span class="muted">None</span>'}</div>
        <div class="section"><h3>Calls</h3>${calls.length ? calls.map((c) => callBlock(c)).join('') : '<span class="muted">No linked calls</span>'}</div>`;
    } else {
      const c = state.calls.find((x) => x.call_id === state.selected.id);
      if (!c) { box.hidden = true; return; }
      const p = state.patients.find((x) => x.patient_id === c.patient_id);
      box.innerHTML = `${closeBtn}<h2>Call ${esc(c.call_id)}</h2><div class="sub">${p ? 'Patient: ' + esc(p.first_name + ' ' + p.last_name) : 'Not linked to a patient'}</div>${callBlock(c)}`;
    }
    $('#close').addEventListener('click', () => { state.selected = null; renderDetail(); });
  }

  function callBlock(c) {
    return `<div style="margin-bottom:10px"><div><span class="badge ${c.status === 'ended' ? 'ok' : 'warn'}">${esc(c.status)}</span> <span class="muted">${fmtDate(c.started_at || c.created_at)} · ${esc(c.ended_reason || '')} · ${c.duration_seconds ? Math.round(c.duration_seconds) + 's' : ''}</span></div>
      ${c.summary ? `<p>${esc(c.summary)}</p>` : ''}
      ${safeUrl(c.recording_url) ? `<div><a href="${esc(safeUrl(c.recording_url))}" target="_blank" rel="noopener">Recording</a></div>` : ''}
      ${c.transcript ? `<details><summary>Transcript</summary><pre>${esc(c.transcript)}</pre></details>` : '<span class="muted">Transcript arrives when the call ends.</span>'}</div>`;
  }

  document.querySelectorAll('.tabs button').forEach((b) => b.addEventListener('click', () => {
    document.querySelectorAll('.tabs button').forEach((x) => x.classList.toggle('active', x === b));
    state.tab = b.dataset.tab; state.selected = null; render();
  }));
  $('#search').addEventListener('input', (e) => { state.q = e.target.value; render(); });
  $('#refresh').addEventListener('click', load);
  load();
  setInterval(load, 30000);
})();
</script>
</body>
</html>
"""


def render_dashboard(*, clinic_name: str) -> str:
    return _PAGE.replace("__CLINIC__", html.escape(clinic_name))
