/* Nature AI Studio — dashboard client. */
'use strict';

const $ = (id) => document.getElementById(id);
const el = (tag, cls, html) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (html != null) node.innerHTML = html;
  return node;
};
const show = (id, on = true) => $(id).classList.toggle('hidden', !on);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtBytes = (b) => {
  if (!b) return '—';
  const u = ['B', 'KB', 'MB', 'GB'];
  let i = 0, n = b;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
};
const fmtTime = (s) => {
  const t = Math.round(s || 0);
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
};
const fmtDate = (ts) => new Date((ts || 0) * 1000).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });

const state = {
  project: null,
  projects: [],
  es: null,
  llm: '',
  llmNode: null,
  openPrompts: new Set(),
  history: [],
  config: {},
  busy: false,
};

/* ── networking ──────────────────────────────────────────────── */

async function api(path, options = {}) {
  const res = await fetch(path, { credentials: 'same-origin', ...options });
  if (res.status === 401) { showGate(); throw new Error('unauthorised'); }
  if (!res.ok) {
    let detail = `${res.status}`;
    try { detail = (await res.json()).error || detail; } catch (_) { /* ignore */ }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

function toast(message, isError = false) {
  const node = $('toast');
  node.textContent = message;
  node.classList.toggle('err', isError);
  node.classList.add('show');
  clearTimeout(node._t);
  node._t = setTimeout(() => node.classList.remove('show'), 3200);
}

/* ── auth gate ───────────────────────────────────────────────── */

function showGate() {
  show('app', false);
  show('gate');
  $('gate-pass').focus();
}

async function boot() {
  let session;
  try {
    session = await api('/api/session');
  } catch (_) {
    return;
  }
  if (session.auth_required && !session.authed) { showGate(); return; }
  show('gate', false);
  show('app');
  state.config = session.config || {};
  applyConfig();

  await Promise.all([loadHistory(), loadProjects()]);
  const saved = localStorage.getItem('nature-studio:project');
  if (saved) {
    openProject(saved).catch(() => localStorage.removeItem('nature-studio:project'));
  } else {
    // Returning on a new device: pick up the most recent project instead of a blank form.
    loadProjects().then(() => {
      const first = document.querySelector('.project-row .btn');
      if (first) openProject(state.projects[0].id).catch(() => {});
    });
  }
  pollHealth();
}

function applyConfig() {
  const cfg = state.config || {};
  const url = cfg.public_url;
  if (url) {
    $('url-text').textContent = url.replace(/^https?:\/\//, '');
    show('url-chip');
  }
  const bits = [];
  if (cfg.model) bits.push(cfg.model);
  if (cfg.hyperframes) bits.push(cfg.hyperframes);
  $('brand-sub').textContent = bits.join(' · ') || 'prompt → images → render';
}

async function pollHealth() {
  const chip = $('health-chip');
  const tick = async () => {
    try {
      const res = await fetch('/api/session', { credentials: 'same-origin' });
      const data = await res.json();
      const cfg = data.config || {};
      if (cfg.public_url && cfg.public_url !== state.config.public_url) {
        state.config = cfg;
        applyConfig();
      }
      chip.textContent = state.config.public_url ? 'online · tunnel live' : 'online · local';
      chip.className = 'status-chip ok';
    } catch (_) {
      chip.textContent = 'offline';
      chip.className = 'status-chip bad';
    }
  };
  tick();
  setInterval(tick, 20000);
}

/* ── console ─────────────────────────────────────────────────── */

function consoleLine(text, level = 'info') {
  show('console');
  const body = $('console-body');
  const node = el('span', `l-${level}`);
  node.textContent = text + '\n';
  body.appendChild(node);
  body.scrollTop = body.scrollHeight;
}

function setStage(text, pct) {
  $('render-status').textContent = pct != null ? `${text} · ${pct}%` : text;
}

function consoleReset() {
  $('console-body').textContent = '';
  state.llm = '';
  state.llmNode = null;
}

function pushLLM(delta, final) {
  if (final) {
    if (state.llmNode) {
      state.llmNode.textContent = state.llm.slice(-4000) + '\n';
      $('console-body').scrollTop = $('console-body').scrollHeight;
    }
    state.llm = '';
    state.llmNode = null;
    return;
  }
  if (!state.llmNode) {
    state.llmNode = el('span', 'l-llm');
    $('console-body').appendChild(state.llmNode);
    show('console');
  }
  state.llm += delta;
  const trimmed = state.llm.length > 4000 ? state.llm.slice(-4000) : state.llm;
  state.llmNode.textContent = trimmed;
  $('console-body').scrollTop = $('console-body').scrollHeight;
}

/* ── projects ────────────────────────────────────────────────── */

async function createProject() {
  const brief = $('brief').value.trim();
  if (!brief) { toast('Describe the video first.', true); return; }
  if (state.busy) return;
  state.busy = true;
  $('analyze-btn').disabled = true;
  $('composer-status').textContent = 'Analysing the brief…';
  consoleReset();
  show('console');
  consoleLine('PHASE 1 — brief analysis & image prompts', 'stage');
  try {
    const project = await api('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        brief,
        duration: Number($('duration').value || 60),
        notes: $('notes').value.trim(),
      }),
    });
    openProject(project.id, project);
  } catch (err) {
    toast(err.message, true);
    $('composer-status').textContent = '';
  } finally {
    state.busy = false;
    $('analyze-btn').disabled = false;
  }
}

async function openProject(id, preset) {
  const project = preset || await api(`/api/projects/${id}`);
  state.project = project;
  localStorage.setItem('nature-studio:project', project.id);
  renderProject(project);
  subscribe(project.id);
  loadProjects();
}

function subscribe(id) {
  if (state.es) state.es.close();
  const es = new EventSource(`/api/projects/${id}/events`);
  state.es = es;

  es.addEventListener('log', (e) => {
    const d = JSON.parse(e.data);
    consoleLine(d.text, d.level || 'info');
  });
  es.addEventListener('llm', (e) => {
    const d = JSON.parse(e.data);
    pushLLM(d.delta || '', !!d.final);
  });
  es.addEventListener('status', (e) => {
    const d = JSON.parse(e.data);
    setStage(d.detail || d.status, d.progress);
    $('composer-status').textContent = d.detail || d.status;
    if (d.status === 'failed') toast(d.detail || 'Job failed', true);
    refreshProject(id);
  });
  es.addEventListener('progress', (e) => {
    const d = JSON.parse(e.data);
    setStage(d.line ? d.line.slice(0, 64) : d.phase, d.progress);
  });
  es.addEventListener('slots', () => refreshProject(id));
  es.addEventListener('video', (e) => {
    const d = JSON.parse(e.data);
    showResult(d.video);
    refreshProject(id);
    loadHistory();
    toast('Video ready — download it or open History');
  });
  es.onerror = () => { /* EventSource auto-reconnects; server replays the backlog */ };
}

async function refreshProject(id) {
  if (!id || (state.project && state.project.id !== id)) return;
  try {
    const project = await api(`/api/projects/${id}`);
    state.project = project;
    renderProject(project);
  } catch (_) { /* project may have been deleted */ }
}

function renderProject(p) {
  const hasSlots = p.slots.length > 0;

  $('composer-status').textContent = p.status === 'planning'
    ? 'Analysing the brief…'
    : `${p.brief.slice(0, 80)}${p.brief.length > 80 ? '…' : ''}`;

  // ── plan ──
  if (p.analysis) {
    show('plan-card');
    const grid = $('plan-grid');
    grid.textContent = '';
    const plan = p.plan || {};
    const cells = [
      ['Hook', plan.hook],
      ['Tone', plan.tone],
      ['Duration', plan.duration ? plan.duration + 's' : `${p.duration}s`],
      ['CTA', plan.cta],
    ];
    for (const [label, value] of cells) {
      if (!value) continue;
      const cell = el('div', 'plan-cell');
      cell.appendChild(el('h4', null, label));
      cell.appendChild(el('p', null, esc(value)));
      grid.appendChild(cell);
    }
    if ((plan.scenes || []).length) {
      const cell = el('div', 'plan-cell scenes');
      cell.appendChild(el('h4', null, `Story arc · ${plan.scenes.length} scenes`));
      cell.appendChild(el('p', null, esc(plan.scenes.join('\n'))));
      grid.appendChild(cell);
    }
    $('plan-raw').textContent = p.analysis;
  }

  // ── prompts ──
  show('prompts-card', hasSlots);
  $('slot-total').textContent = p.slots.length;
  const list = $('prompt-list');
  list.textContent = '';
  p.slots.forEach((slot) => list.appendChild(promptItem(slot)));

  // ── upload ──
  show('upload-card', hasSlots);
  show('render-card', hasSlots);
  $('slot-progress').textContent = `${p.filled} / ${p.total}`;
  $('slot-bar').style.width = p.total ? `${(p.filled / p.total) * 100}%` : '0%';
  renderSlots(p);

  // ── render button ──
  const busy = p.running || ['planning', 'generating_code', 'linting', 'rendering'].includes(p.status);
  const btn = $('render-btn');
  btn.disabled = !p.ready || busy;
  btn.textContent = busy
    ? `⏳ ${p.status.replace(/_/g, ' ')}…`
    : '🎬 Generate code & render video';
  $('reanalyze').disabled = busy;
  $('slot-progress').className = `pill ${p.ready ? 'ok' : ''}`;

  // Reflect the finished run when the page is reopened (no live event fires).
  if (!busy) {
    if (p.status === 'done') setStage('done', 100);
    else if (p.status === 'interrupted') setStage('interrupted - render again');
    else setStage('idle');
  }
  if (p.videos && p.videos.length && $('result').classList.contains('hidden')) {
    showResult(p.videos[p.videos.length - 1]);
  }
}

function promptItem(slot) {
  const item = el('div', `prompt-item ${slot.status === 'ready' ? 'filled' : ''}`);
  if (state.openPrompts.has(slot.idx)) item.classList.add('open');

  const head = el('div', 'prompt-head');
  head.appendChild(el('span', 'prompt-num', String(slot.idx).padStart(2, '0')));
  head.appendChild(el('span', 'prompt-name', esc(slot.filename)));
  head.appendChild(el('span', 'prompt-scene', esc(slot.scene || slot.content || '')));
  if (slot.status === 'ready') head.appendChild(el('span', 'prompt-check', '✓'));
  head.addEventListener('click', () => {
    item.classList.toggle('open');
    if (item.classList.contains('open')) state.openPrompts.add(slot.idx);
    else state.openPrompts.delete(slot.idx);
  });

  const body = el('div', 'prompt-body');
  body.appendChild(el('div', 'prompt-text', esc(slot.prompt || slot.content || 'No prompt text returned for this slot.')));

  const meta = el('div', 'prompt-meta');
  const tags = [slot.resolution, slot.aspect, slot.background ? `bg ${slot.background}` : ''].filter(Boolean);
  tags.forEach((t) => meta.appendChild(el('span', 'tag hex', esc(t))));
  const copy = el('button', 'btn ghost xs', 'Copy prompt');
  copy.addEventListener('click', async (e) => {
    e.stopPropagation();
    await copyText(slot.prompt || slot.content || '');
    toast('Prompt copied to clipboard');
  });
  meta.appendChild(copy);
  body.appendChild(meta);

  item.append(head, body);
  return item;
}

function renderSlots(p) {
  const grid = $('slot-grid');
  grid.textContent = '';
  p.slots.forEach((slot) => {
    const node = el('div', `slot ${slot.status === 'ready' ? 'filled' : ''}`);
    node.dataset.idx = slot.idx;

    if (slot.status === 'ready' && slot.thumb) {
      const img = el('img');
      img.src = slot.thumb + '?v=' + slot.bytes;
      img.alt = slot.filename;
      img.loading = 'lazy';
      node.appendChild(img);
    } else {
      const empty = el('div', 'slot-empty');
      empty.appendChild(el('span', 'plus', '+'));
      empty.appendChild(el('span', null, `<span style="font-size:11.5px;color:var(--muted)">tap to add<br>${esc(slot.filename)}</span>`));
      node.appendChild(empty);
    }

    const label = el('div', 'slot-label');
    label.appendChild(el('span', 'slot-num', String(slot.idx).padStart(2, '0')));
    label.appendChild(el('span', 'slot-name', esc(slot.filename)));
    node.appendChild(label);
    node.appendChild(el('div', 'slot-badge', '✓ added'));

    if (slot.status === 'ready') {
      const clear = el('button', 'slot-clear', 'replace');
      clear.addEventListener('click', (e) => { e.stopPropagation(); pickFile(slot.idx); });
      node.appendChild(clear);
    }

    node.addEventListener('click', () => pickFile(slot.idx));
    node.addEventListener('dragover', (e) => { e.preventDefault(); node.classList.add('dragover'); });
    node.addEventListener('dragleave', () => node.classList.remove('dragover'));
    node.addEventListener('drop', (e) => {
      e.preventDefault();
      node.classList.remove('dragover');
      const file = e.dataTransfer.files[0];
      if (file) uploadFile(slot.idx, file);
    });
    grid.appendChild(node);
  });
}

function pickFile(idx) {
  const input = el('input');
  input.type = 'file';
  input.accept = 'image/png,image/jpeg,image/webp';
  input.addEventListener('change', () => {
    if (input.files[0]) uploadFile(idx, input.files[0]);
  });
  input.click();
}

async function uploadFile(idx, file) {
  if (!/\.(png|jpe?g|webp)$/i.test(file.name)) {
    toast('Only PNG, JPG or WEBP images are supported.', true);
    return;
  }
  const project = state.project;
  if (!project) return;
  const form = new FormData();
  form.append('file', file);
  const node = document.querySelector(`.slot[data-idx="${idx}"]`);
  if (node) node.style.opacity = '0.5';
  try {
    const updated = await api(`/api/projects/${project.id}/slots/${idx}/image`, { method: 'POST', body: form });
    state.project = updated;
    renderProject(updated);
    toast(`Image ${idx} added`);
    const next = document.querySelector('.slot:not(.filled)');
    if (next && window.innerWidth > 720) next.scrollIntoView({ behavior: 'smooth', block: 'center' });
  } catch (err) {
    toast(err.message, true);
  } finally {
    if (node) node.style.opacity = '';
  }
}

function fillNextEmpty() {
  const empty = document.querySelector('.slot:not(.filled)');
  if (!empty) { toast('All boxes are filled ✓'); return; }
  empty.scrollIntoView({ behavior: 'smooth', block: 'center' });
  empty.click();
}

async function triggerRender() {
  const project = state.project;
  if (!project || !project.ready) return;
  show('console');
  consoleReset();
  consoleLine('PHASE 2/3 — generating animation code, linting, rendering', 'stage');
  try {
    await api(`/api/projects/${project.id}/render`, { method: 'POST' });
    toast('Render started');
    refreshProject(project.id);
  } catch (err) {
    toast(err.message, true);
  }
}

function showResult(video) {
  if (!video) return;
  const fileUrl = video.file_url || `/api/videos/${video.id}/file`;
  const downloadUrl = video.download_url || `/api/videos/${video.id}/download`;
  show('result');
  const node = $('result-video');
  if (node.getAttribute('src') !== fileUrl) {
    node.src = fileUrl;
    node.load();
  }
  $('result-download').href = downloadUrl;
  $('result-download').setAttribute('download', video.filename || 'video.mp4');
  $('result-meta').textContent =
    `${video.filename} · ${video.width}×${video.height} · ${fmtTime(video.duration)} · ${fmtBytes(video.bytes)}`;
  setStage('done', 100);
}

async function openSource() {
  if (!state.project) return;
  try {
    const data = await api(`/api/projects/${state.project.id}/source`);
    $('source-body').textContent = data.index_html || '(no composition yet)';
    show('source-details');
    $('source-details').open = true;
    $('source-details').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (err) {
    toast(err.message, true);
  }
}

/* ── project list ────────────────────────────────────────────── */

async function loadProjects() {
  let projects = [];
  try { projects = await api('/api/projects'); } catch (_) { return; }
  state.projects = projects;
  show('projects-card', projects.length > 0);
  const list = $('project-list');
  list.textContent = '';
  if (!projects.length) return;

  projects.forEach((p) => {
    const row = el('div', 'project-row');
    const main = el('div', 'p-main');
    main.appendChild(el('div', 'p-brief', esc(p.brief)));
    main.appendChild(el('div', 'p-sub',
      `${p.status.replace(/_/g, ' ')} · ${p.filled}/${p.total} images · ${fmtDate(p.created_at)}`));
    row.appendChild(main);

    row.appendChild(el('span', 'pill', `${p.duration}s`));

    const actions = el('div', 'head-actions');
    const open = el('button', 'btn ghost sm', 'Open');
    open.addEventListener('click', () => openProject(p.id));
    const del = el('button', 'btn ghost sm danger', 'Delete');
    del.addEventListener('click', async () => {
      if (!confirm(`Delete "${p.brief.slice(0, 50)}" and its folder?`)) return;
      try {
        await api(`/api/projects/${p.id}`, { method: 'DELETE' });
        if (state.project && state.project.id === p.id) {
          state.project = null;
          localStorage.removeItem('nature-studio:project');
          if (state.es) { state.es.close(); state.es = null; }
        }
        loadProjects();
        toast('Project deleted');
      } catch (err) { toast(err.message, true); }
    });
    actions.append(open, del);
    row.appendChild(actions);
    list.appendChild(row);
  });
}

/* ── history ─────────────────────────────────────────────────── */

async function loadHistory() {
  try {
    state.history = await api('/api/history');
  } catch (_) { return; }
  $('history-count').textContent = state.history.length;
  $('history-total').textContent = state.history.length;

  const grid = $('history-grid');
  grid.textContent = '';
  if (!state.history.length) {
    grid.appendChild(el('div', 'empty', 'No videos yet. Render your first project →'));
    return;
  }

  state.history.forEach((v) => {
    const card = el('div', 'history-card');
    const video = el('video');
    video.src = v.file_url;
    video.controls = true;
    video.preload = 'metadata';
    video.playsInline = true;
    card.appendChild(video);

    const body = el('div', 'history-body');
    body.appendChild(el('div', 'history-title', esc(v.brief || v.filename)));
    const meta = el('div', 'history-meta');
    meta.appendChild(el('span', null, fmtTime(v.duration)));
    if (v.width) meta.appendChild(el('span', null, `${v.width}×${v.height}`));
    meta.appendChild(el('span', null, fmtBytes(v.bytes)));
    meta.appendChild(el('span', null, fmtDate(v.created_at)));
    body.appendChild(meta);

    const actions = el('div', 'history-actions');
    const dl = el('a', 'btn primary sm', '⬇ Download');
    dl.href = v.download_url;
    dl.setAttribute('download', v.filename);
    const del = el('button', 'btn ghost sm danger', 'Delete');
    del.addEventListener('click', async () => {
      if (!confirm(`Delete "${v.filename}"?`)) return;
      try {
        await api(`/api/videos/${v.id}?keep_file=false`, { method: 'DELETE' });
        loadHistory();
        toast('Video deleted');
      } catch (err) { toast(err.message, true); }
    });
    actions.append(dl, del);
    body.appendChild(actions);
    card.appendChild(body);
    grid.appendChild(card);
  });
}

function switchTab(name) {
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === name));
  show('view-compose', name === 'compose');
  show('view-history', name === 'history');
  if (name === 'history') loadHistory();
}

async function copyText(text) {
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
  } catch (_) {
    const area = el('textarea');
    area.value = text;
    document.body.appendChild(area);
    area.select();
    document.execCommand('copy');
    area.remove();
  }
}

/* ── wiring ──────────────────────────────────────────────────── */

function init() {
  $('gate-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      await api('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: $('gate-pass').value }),
      });
      show('gate', false);
      boot();
    } catch (_) {
      show('gate-error');
    }
  });

  $('analyze-btn').addEventListener('click', createProject);
  $('brief').addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') createProject();
  });
  $('render-btn').addEventListener('click', triggerRender);
  $('upload-next').addEventListener('click', fillNextEmpty);
  $('result-open').addEventListener('click', openSource);
  $('result-history').addEventListener('click', () => switchTab('history'));
  $('console-clear').addEventListener('click', consoleReset);
  $('history-refresh').addEventListener('click', loadHistory);
  $('history-import').addEventListener('click', async () => {
    try {
      const res = await api('/api/history/import', { method: 'POST' });
      await loadHistory();
      toast(res.added.length ? `Imported ${res.added.length} video(s)` : 'No new videos found');
    } catch (err) { toast(err.message, true); }
  });
  $('refresh-btn').addEventListener('click', () => { loadHistory(); loadProjects(); if (state.project) refreshProject(state.project.id); });

  $('copy-plan').addEventListener('click', async () => {
    if (!state.project) return;
    await copyText(state.project.analysis || '');
    toast('Story plan copied');
  });

  $('copy-prompts').addEventListener('click', async () => {
    if (!state.project) return;
    const text = state.project.slots
      .map((s) => `IMAGE ${s.idx} - ${s.filename}\nScene: ${s.scene}\n${s.prompt || s.content}`)
      .join('\n\n');
    await copyText(text);
    toast(`${state.project.slots.length} prompts copied`);
  });

  let allOpen = false;
  $('toggle-prompt-view').addEventListener('click', (e) => {
    allOpen = !allOpen;
    document.querySelectorAll('.prompt-item').forEach((item) => item.classList.toggle('open', allOpen));
    e.target.textContent = allOpen ? 'Collapse all' : 'Expand all';
  });

  $('reanalyze').addEventListener('click', async () => {
    if (!state.project) return;
    if (!confirm('Regenerate the story plan and image manifest? Uploaded images will be detached.')) return;
    try {
      const updated = await api(`/api/projects/${state.project.id}/analyze`, { method: 'POST' });
      state.project = updated;
      renderProject(updated);
      consoleReset();
      consoleLine('PHASE 1 — re-analysing brief', 'stage');
    } catch (err) { toast(err.message, true); }
  });

  $('url-chip').addEventListener('click', async () => {
    await copyText(state.config.public_url || location.origin);
    toast('Public URL copied');
  });

  document.querySelectorAll('.tab').forEach((tab) => {
    tab.addEventListener('click', () => switchTab(tab.dataset.tab));
  });

  document.addEventListener('paste', (e) => {
    if (!state.project) return;
    const item = [...(e.clipboardData?.items || [])].find((i) => i.type.startsWith('image/'));
    if (!item) return;
    const empty = document.querySelector('.slot:not(.filled)');
    if (!empty) return;
    uploadFile(Number(empty.dataset.idx), item.getAsFile());
  });

  boot();
}

document.addEventListener('DOMContentLoaded', init);