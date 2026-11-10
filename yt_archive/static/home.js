const form = document.getElementById('get-form');
const status = document.getElementById('status');
const urlBox = document.getElementById('url');
const countsEl = document.getElementById('queue-counts');
const rowsEl = document.getElementById('queue-rows');
const expanded = new Set();
const NEEDS = JSON.parse(document.getElementById('page-data').textContent).needs;
let allWaiting = false;
let openSeen = null;
function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function note(text, kind, href) {
  const row = document.createElement(href ? 'a' : 'div');
  row.className = 'note' + (kind ? ' ' + kind : '');
  row.textContent = text;
  if (href) { row.href = href; row.style.display = 'block'; }
  status.append(row);
}
document.getElementById('browse-creator').onclick = () => {
  const source = urlBox.value.trim().split(/\s+/)[0];
  window.location.assign(source ? '/browse?' + new URLSearchParams({source}) : '/browse');
};
const bookmarklet = document.getElementById('bookmarklet');
if (bookmarklet) {
  const code = '(()=>{const u=new URL(' + JSON.stringify(location.origin + '/add') + ');'
    + "u.searchParams.set('url',location.href);"
    + "u.searchParams.set('title',document.title);"
    + "u.searchParams.set('key'," + JSON.stringify(bookmarklet.dataset.key) + ');'
    + "if(!window.open(u.href,'_blank','popup,width=560,height=340'))"
    + "alert('ytarchive: allow pop-ups on this site to queue videos');})()";
  bookmarklet.href = 'javascript:' + encodeURIComponent(code);
  bookmarklet.onclick = (e) => {
    e.preventDefault();
    status.replaceChildren();
    note('Drag that button to your bookmarks bar, then click it on a YouTube page, a tweet, or an X profile.');
  };
}
async function post(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const urls = urlBox.value.split(/\s+/).map((s) => s.trim()).filter(Boolean);
  if (!urls.length) return;
  urlBox.value = '';
  urlBox.focus();
  status.replaceChildren();
  const creators = document.getElementById('creator-results');
  creators.replaceChildren();
  for (const url of urls) {
    try {
      const data = await post('/api/get', {url, via: 'pasted'});
      if (data.browse_url) {
        if (urls.length === 1) { window.location.assign(data.browse_url); return; }
        const link = document.createElement('a');
        link.href = data.browse_url;
        link.textContent = 'Browse ' + url;
        const row = document.createElement('p');
        row.append(link);
        creators.append(row);
        continue;
      }
      const name = data.title || data.video_id;
      if (data.status === 'complete') note('Already archived and up to date: ' + name, 'done', '/v/' + data.video_id);
      else if (data.existing) note('Already in the queue: ' + name);
      else note('Added to the queue: ' + name);
      if (data.playlist_browse_url) note('That link is from a playlist. Choose more from the playlist →', '', data.playlist_browse_url);
    } catch (err) {
      note(url + ' — ' + err.message, 'error');
    }
  }
  paintQueue();
});
const STEPS = [['download', 'Download'], ['shots', 'Shots'], ['audio', 'MP3']];
function steps(j) {
  return STEPS.map(([key, label]) => {
    const state = j[key] || 'pending';
    let text = esc(label);
    if (state === 'running' && j.progress != null) text += ' <span class="pct">' + Math.floor(j.progress * 100) + '%</span>';
    if (state === 'done' || state === 'skipped') text = '✓ ' + text;
    return '<span class="step ' + state + '">' + text + '</span>';
  }).join('');
}
function clock(seconds) {
  seconds = Math.max(0, Math.floor(seconds));
  const h = Math.floor(seconds / 3600), m = Math.floor(seconds / 60) % 60, s = seconds % 60;
  return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(s).padStart(2, '0');
}
function when(iso) {
  const date = new Date(iso);
  if (!iso || Number.isNaN(date.getTime())) return '';
  const pad = (n) => String(n).padStart(2, '0');
  const today = new Date().toDateString() === date.toDateString();
  return (today ? '' : (date.getMonth() + 1) + '/' + date.getDate() + ' ')
    + pad(date.getHours()) + ':' + pad(date.getMinutes());
}
function titleHtml(j) {
  const text = j.title ? esc(j.title) : '<span class="qid">' + esc(j.video_id) + '</span>';
  return j.archived
    ? '<a class="qtitle" href="/v/' + esc(j.video_id) + '" title="' + esc(j.title || j.video_id) + '">' + text + '</a>'
    : '<span class="qtitle" title="' + esc(j.url) + '">' + text + '</span>';
}
function row(kind, j, state, detail, time, actions) {
  const open = expanded.has(j.job_id) ? ' expanded' : '';
  return '<div class="qrow ' + kind + open + '" data-job="' + esc(j.job_id) + '" data-url="' + esc(j.url) + '">'
    + '<span class="qstate">' + state + '</span>' + titleHtml(j)
    + '<span class="qdetail" title="' + esc(j.error || j.message || '') + '">' + detail + '</span>'
    + '<span class="qtime"' + time + '</span>'
    + '<span class="qact">' + (actions || '') + '</span></div>';
}
function render(q) {
  const parts = [];
  if (q.running.length) parts.push('<span class="working"><strong>' + q.running.length + '</strong>working</span>');
  if (q.waiting_count) parts.push('<span><strong>' + q.waiting_count + '</strong>waiting</span>');
  if (q.failed.length) parts.push('<span class="failed"><strong>' + q.failed.length + '</strong>failed</span>');
  if (q.waiting_count > 1) parts.push('<button type="button" class="btn" data-act="clear">Remove all waiting</button>');
  countsEl.innerHTML = parts.join('');
  document.getElementById('queue').hidden = !(q.running.length || q.waiting_count || q.failed.length);
  const rows = [];
  q.running.forEach((j) => rows.push(row('running', j, 'Working', steps(j) + ' ' + esc(j.message || j.phase),
    ' data-since="' + esc(j.started_at) + '">', '')));
  q.waiting.forEach((j, i) => rows.push(row('waiting', j,
    '<span class="qpos">#' + (i + 1) + '</span>waiting',
    esc(j.message || (j.needs.length ? j.needs.map((n) => NEEDS[n] || n).join(' · ') : j.archived ? '' : 'download')), '>',
    '<button type="button" class="btn" data-act="remove">Remove</button>')));
  if (q.waiting_count > q.waiting.length) {
    rows.push('<div class="qmore">+ ' + (q.waiting_count - q.waiting.length) + ' more waiting · '
      + '<button type="button" class="btn" data-act="all-waiting">Show all</button></div>');
  } else if (allWaiting && q.waiting_count > 8) {
    rows.push('<div class="qmore"><button type="button" class="btn" data-act="few-waiting">Show fewer</button></div>');
  }
  q.failed.forEach((j) => rows.push(row('error', j, 'Failed', esc(j.error || 'failed'),
    '>' + esc(when(j.finished_at)),
    '<button type="button" class="btn" data-act="retry">Retry</button>'
    + '<button type="button" class="btn" data-act="dismiss">Dismiss</button>')));
  rowsEl.innerHTML = rows.join('');
  tick();
}
function tick() {
  rowsEl.querySelectorAll('.qtime[data-since]').forEach((el) => {
    const since = Date.parse(el.dataset.since);
    el.textContent = Number.isNaN(since) ? '' : clock((Date.now() - since) / 1000);
  });
}
setInterval(tick, 1000);
document.getElementById('queue').addEventListener('click', async (e) => {
  const button = e.target.closest('button[data-act]');
  const line = e.target.closest('.qrow');
  if (button && button.dataset.act.endsWith('-waiting') && button.dataset.act !== 'clear-waiting') {
    allWaiting = button.dataset.act === 'all-waiting';
    paintQueue();
    return;
  }
  if (!button) {
    if (line && line.classList.contains('error') && e.target.closest('.qdetail')) {
      const id = line.dataset.job;
      expanded.has(id) ? expanded.delete(id) : expanded.add(id);
      line.classList.toggle('expanded');
    }
    return;
  }
  button.disabled = true;
  const id = line ? encodeURIComponent(line.dataset.job) : '';
  try {
    if (button.dataset.act === 'clear') await post('/api/jobs/clear-waiting');
    if (button.dataset.act === 'remove') await post('/api/jobs/' + id + '/remove');
    if (button.dataset.act === 'dismiss') await post('/api/jobs/' + id + '/dismiss');
    if (button.dataset.act === 'retry') {
      const data = await post('/api/get', {url: line.dataset.url});
      if (data.status === 'complete') await post('/api/jobs/' + id + '/dismiss');
    }
  } catch (err) {
    status.replaceChildren();
    note(err.message, 'error');
  }
  paintQueue();
});
document.addEventListener('click', async (e) => {
  const button = e.target.closest('#update-archive');
  if (!button) return;
  button.disabled = true;
  const out = document.getElementById('update-status');
  out.textContent = 'queueing…';
  try {
    const data = await post('/api/update-archive');
    out.textContent = data.queued ? 'queued ' + data.queued : 'already queued';
  } catch (err) {
    out.textContent = err.message;
    button.disabled = false;
  }
  paintQueue();
});
async function refreshArchive() {
  const res = await fetch(window.location.pathname + window.location.search, {cache: 'no-store'});
  if (!res.ok) return;
  const page = new DOMParser().parseFromString(await res.text(), 'text/html');
  for (const id of ['archive-count', 'archive-health', 'archive-list']) {
    const fresh = page.getElementById(id), old = document.getElementById(id);
    if (fresh && old) old.replaceWith(fresh);
  }
  localizeTimes(document.getElementById('archive-list'));
}
async function paintQueue() {
  const res = await fetch('/api/jobs' + (allWaiting ? '?waiting=1000' : ''), {cache: 'no-store'});
  if (!res.ok) throw new Error(res.statusText);
  const q = await res.json();
  render(q);
  // A job starting, finishing, or being added or removed changes what the archive list shows.
  const open = q.running.map((j) => j.job_id).join(',') + '|' + q.waiting_count;
  if (openSeen !== null && open !== openSeen) refreshArchive();
  openSeen = open;
  return Boolean(q.running.length || q.waiting_count);
}
(async function loop() {
  const busy = await paintQueue().catch(() => false);
  setTimeout(loop, document.hidden ? 15000 : busy ? 1000 : 5000);
})();
