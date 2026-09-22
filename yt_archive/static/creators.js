(() => {
  'use strict';
  const el = (id) => document.getElementById('creator-' + id);
  const selected = new Set();
  let items = [], lookupId = '', page = 0, generation = 0, busy = false;
  const pageSize = 100;
  const available = (item) => !item.archived && !item.queued && !item.unavailable;
  const matches = () => {
    const query = el('filter').value.trim().toLocaleLowerCase();
    return items.filter((item) => (item.title + ' ' + item.video_id).toLocaleLowerCase().includes(query));
  };
  function status(message, kind = '') {
    el('status').textContent = message;
    el('status').className = kind;
  }
  async function request(url, body) {
    const response = await fetch(url, body === undefined ? {} : {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || response.statusText);
    return data;
  }
  function updateActions() {
    const count = items.filter(available).length;
    el('get-selected').textContent = 'Get selected (' + selected.size + ')';
    el('get-selected').disabled = busy || !selected.size;
    el('get-all').textContent = 'Get all available (' + count + ')';
    el('get-all').disabled = busy || !count;
    el('selected-count').textContent = selected.size + ' selected across all pages';
    el('select').disabled = busy || !matches().some(available);
    el('clear').disabled = busy || !selected.size;
    el('load').disabled = busy;
  }
  function duration(seconds) {
    if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return '';
    const total = Math.floor(seconds), hours = Math.floor(total / 3600);
    const minutes = Math.floor(total / 60) % 60, secs = String(total % 60).padStart(2, '0');
    return hours ? hours + ':' + String(minutes).padStart(2, '0') + ':' + secs : minutes + ':' + secs;
  }
  function render() {
    const filtered = matches();
    const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
    page = Math.min(page, pages - 1);
    const rows = document.createDocumentFragment();
    for (const item of filtered.slice(page * pageSize, (page + 1) * pageSize)) {
      const row = document.createElement('article');
      row.className = 'creator-item';
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.id = 'choose-' + item.video_id;
      checkbox.checked = selected.has(item.video_id);
      checkbox.disabled = busy || !available(item);
      checkbox.addEventListener('change', () => {
        if (checkbox.checked) selected.add(item.video_id); else selected.delete(item.video_id);
        updateActions();
      });
      const image = document.createElement('img');
      image.src = 'https://i.ytimg.com/vi/' + item.video_id + '/mqdefault.jpg';
      image.loading = 'lazy';
      image.alt = '';
      const info = document.createElement('div');
      const title = document.createElement('label');
      title.htmlFor = checkbox.id;
      title.textContent = item.title;
      const meta = document.createElement('div');
      meta.className = 'meta';
      const state = item.archived ? 'Saved' : item.queued ? 'Queued' : item.unavailable || 'Available';
      meta.textContent = [duration(item.duration), state].filter(Boolean).join(' · ');
      const link = document.createElement('a');
      link.textContent = item.archived ? 'Open archive' : 'Watch on YouTube';
      link.href = item.archived ? '/v/' + item.video_id : 'https://www.youtube.com/watch?v=' + item.video_id;
      link.target = '_blank';
      link.rel = 'noopener';
      info.append(title, meta, link);
      row.append(checkbox, image, info);
      rows.append(row);
    }
    if (!filtered.length) {
      const empty = document.createElement('p');
      empty.textContent = items.length ? 'No matching videos.' : 'No videos were listed for this creator.';
      rows.append(empty);
    }
    el('items').replaceChildren(rows);
    el('count').textContent = filtered.length + ' matching / ' + items.length + ' listed · '
      + items.filter((item) => item.archived).length + ' saved · '
      + items.filter((item) => item.queued && !item.archived).length + ' queued';
    el('page').textContent = 'Page ' + (page + 1) + ' of ' + pages;
    el('prev').disabled = page === 0;
    el('next').disabled = page + 1 >= pages;
    updateActions();
  }
  async function browse() {
    if (busy) return;
    const source = el('source').value.trim();
    if (!source) return;
    const current = ++generation;
    items = []; selected.clear(); lookupId = ''; page = 0;
    el('filter').value = '';
    el('picker').hidden = true;
    el('warning').textContent = '';
    status('Loading creator videos… Large channels can take a few minutes. No downloads have started.');
    history.replaceState(null, '', '/browse?' + new URLSearchParams({source}));
    try {
      const started = await request('/api/creators', {source});
      if (current !== generation) return;
      lookupId = started.lookup_id;
      let listing;
      while (current === generation) {
        listing = await request('/api/creators/' + lookupId);
        if (current !== generation) return;
        if (listing.status === 'error') throw new Error(listing.error);
        if (listing.status === 'done') break;
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
      if (current !== generation) return;
      items = listing.items;
      el('title').textContent = listing.title;
      el('youtube').href = listing.url;
      el('warning').textContent = listing.warning;
      el('picker').hidden = false;
      status('Choose videos below, or get all available. Downloads run one at a time.');
      render();
    } catch (error) {
      if (current === generation) status(error.message, 'error');
    }
  }
  async function enqueue(all) {
    if (busy) return;
    busy = true;
    render();
    status('Adding videos to the download queue…');
    try {
      const result = await request('/api/creators/' + lookupId + '/get',
        all ? {all: true} : {video_ids: Array.from(selected)});
      const queued = new Set(result.video_ids);
      for (const item of items) if (queued.has(item.video_id)) item.queued = true;
      selected.clear();
      status(result.queued + (result.queued === 1 ? ' video queued. ' : ' videos queued. ')
        + result.skipped + ' already saved or queued. '
        + 'You can leave this page; the queue keeps running.', 'done');
      // Refresh saved/queued flags without repeating the remote lookup.
      try { items = (await request('/api/creators/' + lookupId)).items; } catch (_) { /* keep local result */ }
    } catch (error) {
      status(error.message + ' You can retry; already queued videos will be skipped.', 'error');
    } finally {
      busy = false;
      render();
    }
  }
  el('form').addEventListener('submit', (event) => { event.preventDefault(); browse(); });
  el('filter').addEventListener('input', () => { page = 0; render(); });
  el('select').onclick = () => { matches().filter(available).forEach((item) => selected.add(item.video_id)); render(); };
  el('clear').onclick = () => { selected.clear(); render(); };
  el('get-selected').onclick = () => enqueue(false);
  el('get-all').onclick = () => enqueue(true);
  el('prev').onclick = () => { page--; render(); el('count').scrollIntoView({block: 'start'}); };
  el('next').onclick = () => { page++; render(); el('count').scrollIntoView({block: 'start'}); };
  if (el('source').value.trim()) browse();
})();
