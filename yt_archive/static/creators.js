// The picker on /browse (docs/browsing.md, "Browse YouTube"). Lists come from the server's
// kept copy; the server asks YouTube only when it has none or when Refresh is pressed.
(() => {
  'use strict';
  const el = (id) => document.getElementById('creator-' + id);
  const PAGE = 100;
  const CONFIRM_OVER = 50;
  const DOWNLOADS_PER_HOUR = Number(el('picker').dataset.downloadsPerHour);
  const SECTIONS = [['videos', 'Videos'], ['shorts', 'Shorts'], ['live', 'Live'], ['all', 'All']];
  const KINDS = {channel: 'creator', playlists: 'playlists', playlist: 'playlist'};
  // [value, direction, tie order]: missing values sort last; ties keep YouTube's order,
  // which lists newest first, so "oldest first" breaks ties the other way.
  const SORTS = {
    new: [(item) => item.timestamp, -1, 1],
    old: [(item) => item.timestamp, 1, -1],
    long: [(item) => item.duration, -1, 1],
    short: [(item) => item.duration, 1, 1],
    views: [(item) => item.views, -1, 1],
    title: [(item) => item.title.toLocaleLowerCase(), 1, 1],
  };
  const FILTERS = [['q', 'filter'], ['len', 'length'], ['age', 'age'], ['sort', 'sort']];
  const number = new Intl.NumberFormat();
  const compact = new Intl.NumberFormat(undefined, {notation: 'compact', maximumFractionDigits: 1});
  const params = new URLSearchParams(location.search);
  const selected = new Set();
  let listing = null, items = [], shown = [], playlists = null, playlistsLoading = false;
  let section = params.get('section') || '';
  let page = 0, busy = false, generation = 0, anchor = -1, focus = -1, one = null;

  for (const [key, id] of FILTERS) {
    const value = params.get(key);
    const box = el(id);
    if (value !== null && (box.tagName !== 'SELECT' || [...box.options].some((o) => o.value === value))) box.value = value;
  }
  el('hide').checked = params.get('hide') === '1';

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const available = (item) => !item.archived && !item.queued && !item.unavailable;
  const watchUrl = (id) => 'https://www.youtube.com/watch?v=' + id;
  const thumbUrl = (id, size) => 'https://i.ytimg.com/vi/' + id + '/' + size + '.jpg';
  const browseHref = (source) => '/browse?' + new URLSearchParams({source});

  function node(tag, className, text) {
    const made = document.createElement(tag);
    if (className) made.className = className;
    if (text !== undefined) made.textContent = text;
    return made;
  }
  function strong(text) { return node('strong', '', text); }
  function link(text, href, outside) {
    const a = node('a', '', text);
    a.href = href;
    if (outside) { a.target = '_blank'; a.rel = 'noopener noreferrer'; }
    return a;
  }
  function duration(seconds) {
    if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return '';
    const total = Math.floor(seconds), hours = Math.floor(total / 3600);
    const minutes = Math.floor(total / 60) % 60, secs = String(total % 60).padStart(2, '0');
    return hours ? hours + ':' + String(minutes).padStart(2, '0') + ':' + secs : minutes + ':' + secs;
  }
  // Upload dates come from YouTube's "3 weeks ago" text, so they are shown that coarsely.
  function age(stamp) {
    if (typeof stamp !== 'number') return '';
    const days = Math.max(0, (Date.now() / 1000 - stamp) / 86400);
    if (days < 1) return 'today';
    if (days < 7) return Math.floor(days) + ' d';
    if (days < 31) return Math.floor(days / 7) + ' wk';
    if (days < 365) return Math.floor(days / 30.44) + ' mo';
    return Math.floor(days / 365.25) + ' yr';
  }
  function listed(iso) {
    const minutes = Math.max(0, (Date.now() - Date.parse(iso)) / 60000);
    if (!Number.isFinite(minutes)) return [];
    if (minutes < 1) return ['listed just now'];
    const [value, unit] = minutes < 60 ? [minutes, ' min'] : minutes < 48 * 60 ? [minutes / 60, ' h'] : [minutes / 1440, ' d'];
    return ['listed ', strong(String(Math.floor(value))), unit + ' ago'];
  }
  function status(parts, kind = '', retry = false) {
    const box = el('status');
    box.replaceChildren(...[].concat(parts).filter((part) => part !== ''));
    box.className = kind;
    if (retry) {
      const again = node('button', 'btn', 'Try again');
      again.type = 'button';
      again.onclick = () => browse(true);
      box.append(' ', again);
    }
  }
  function progress(state) {
    const parts = ['Reading ', state.title ? '“' + state.title + '”' : 'the list',
      state.account ? ' from YouTube with your account: ' : ' from YouTube: ', strong(number.format(state.found || 0))];
    if (state.expected) parts.push(' of ', strong(number.format(state.expected)));
    parts.push(' found, ', strong(number.format(state.requests || 1)),
      state.requests === 1 ? ' request so far. ' : ' requests so far. ', 'Nothing is downloaded.');
    return parts;
  }

  async function request(url, body) {
    const response = await fetch(url, body === undefined ? {} : {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || response.statusText);
    return data;
  }
  // Opens a list on the server (kept, or fetched once for everyone) and waits for it.
  async function load(source, refresh, onProgress, current) {
    let state = await request('/api/creators', {source, refresh});
    while (state.status === 'loading') {
      onProgress(state);
      await sleep(1000);
      if (current !== generation) return null;
      state = await request('/api/creators/' + state.lookup_id);
    }
    if (state.status === 'error') {
      const failure = new Error(state.error);
      failure.retry = true;
      throw failure;
    }
    const full = await request('/api/creators/' + state.lookup_id);
    full.error = state.error || full.error;
    return full;
  }

  function selectionKey() { return 'ytarchive-selection:' + listing.lookup_id; }
  function saveSelection() {
    try { sessionStorage.setItem(selectionKey(), JSON.stringify([...selected])); } catch (_) { /* private mode */ }
  }
  function restoreSelection() {
    let saved = [];
    try { saved = JSON.parse(sessionStorage.getItem(selectionKey()) || '[]'); } catch (_) { /* none */ }
    const choosable = new Set(items.filter(available).map((item) => item.video_id));
    for (const id of [...selected, ...saved]) if (choosable.has(id)) selected.add(id);
    for (const id of [...selected]) if (!choosable.has(id)) selected.delete(id);
  }

  function remember() {
    const next = new URLSearchParams({source: listing ? listing.url : el('source').value.trim()});
    if (listing && listing.kind === 'channel' && section) next.set('section', section);
    for (const [key, id] of FILTERS) if (el(id).value.trim()) next.set(key, el(id).value.trim());
    if (el('hide').checked) next.set('hide', '1');
    history.replaceState(null, '', '/browse?' + next);
  }

  async function browse(refresh = false) {
    const source = refresh && listing ? listing.url : el('source').value.trim();
    if (!source) return;
    const current = ++generation;
    if (!refresh) {
      listing = null; items = []; shown = []; playlists = null; playlistsLoading = false; one = null;
      selected.clear();
      el('picker').hidden = true;
    }
    busy = true;
    if (listing) paintBar();
    status(refresh ? 'Asking YouTube for the current list…' : 'Opening the list…');
    try {
      const result = await load(source, refresh, (state) => status(progress(state)), current);
      if (!result || current !== generation) return;
      listing = result;
      items = result.items;
      section = defaultSection();
      restoreSelection();
      el('source').value = listing.url;
      el('picker').hidden = false;
      status(result.error ? result.error : '', result.error ? 'error' : '');
      busy = false;
      render();
      if (section === 'playlists') loadPlaylists();
    } catch (error) {
      if (current === generation) status(error.message, 'error', !!error.retry);
    } finally {
      if (current === generation) {
        busy = false;
        if (listing) paintBar();
      }
    }
  }

  function defaultSection() {
    if (listing.kind !== 'channel') return '';
    const has = (key) => items.some((item) => item.section === key);
    if (section === 'all' || section === 'playlists' || (SECTIONS.some(([key]) => key === section) && has(section))) return section;
    const first = SECTIONS.find(([key]) => key !== 'all' && has(key));
    return first ? first[0] : 'all';
  }

  function render() {
    if (!listing) return;
    remember();
    renderHead();
    renderTabs();
    const playlistsView = listing.kind === 'playlists' || section === 'playlists';
    el('videos').hidden = playlistsView || !!one;
    el('one').hidden = playlistsView || !one;
    el('playlists').hidden = !playlistsView;
    if (playlistsView) renderPlaylists();
    else if (one) paintOne();
    else renderList();
  }

  function renderHead() {
    el('title').textContent = listing.title || listing.channel || 'Untitled';
    el('kind').textContent = KINDS[listing.kind];
    const owner = el('owner');
    owner.hidden = true;
    owner.removeAttribute('href');
    if (listing.kind === 'playlist' && listing.channel) {
      owner.hidden = false;
      owner.textContent = 'by ' + listing.channel;
      if (listing.channel_id) owner.href = browseHref('https://www.youtube.com/channel/' + listing.channel_id);
    } else if (listing.kind === 'playlists') {
      owner.hidden = false;
      owner.textContent = 'all videos by ' + (listing.channel || 'this creator');
      owner.href = browseHref(listing.url.replace(/\/playlists$/, ''));
    }
    el('youtube').href = listing.url;
    el('listed').replaceChildren(...listed(listing.fetched_at), listing.account ? ' with your YouTube account' : '');
    el('listed').title = new Date(listing.fetched_at).toLocaleString();
    el('refresh').disabled = busy;
    el('warning').textContent = listing.warning || '';
  }

  function renderTabs() {
    const tabs = el('tabs');
    tabs.hidden = listing.kind !== 'channel';
    if (tabs.hidden) return;
    const counts = {all: items.length};
    for (const item of items) counts[item.section] = (counts[item.section] || 0) + 1;
    tabs.replaceChildren(
      ...SECTIONS.map(([key, label]) => tab(key, label, counts[key] || 0)),
      tab('playlists', 'Playlists', playlists ? playlists.items.length : null),
    );
  }
  function tab(key, label, count) {
    const button = node('button', 'pick-tab' + (section === key ? ' on' : ''));
    button.type = 'button';
    button.setAttribute('aria-pressed', String(section === key));
    button.disabled = count === 0;
    button.append(node('span', '', label));
    if (count !== null) button.append(strong(number.format(count)));
    button.onclick = () => {
      section = key;
      page = 0; anchor = -1; focus = -1; one = null;
      render();
      if (key === 'playlists') loadPlaylists();
    };
    return button;
  }

  function range(value) {
    const [low, high] = (value || '-').split('-');
    return [low === '' ? null : Number(low), high === '' ? null : Number(high)];
  }
  function view() {
    const query = el('filter').value.trim().toLocaleLowerCase();
    const [minLength, maxLength] = range(el('length').value);
    const [minDays, maxDays] = range(el('age').value);
    const hide = el('hide').checked;
    const now = Date.now() / 1000;
    const rows = items.filter((item) => {
      if (listing.kind === 'channel' && section !== 'all' && item.section !== section) return false;
      if (hide && (item.archived || item.queued)) return false;
      if (query && !(item.title + ' ' + item.video_id).toLocaleLowerCase().includes(query)) return false;
      if (minLength !== null || maxLength !== null) {
        if (typeof item.duration !== 'number') return false;
        if (minLength !== null && item.duration < minLength) return false;
        if (maxLength !== null && item.duration >= maxLength) return false;
      }
      if (minDays !== null || maxDays !== null) {
        if (typeof item.timestamp !== 'number') return false;
        const days = (now - item.timestamp) / 86400;
        if (minDays !== null && days < minDays) return false;
        if (maxDays !== null && days > maxDays) return false;
      }
      return true;
    });
    const sort = SORTS[el('sort').value];
    if (!sort) return rows;
    const [value, direction, tie] = sort;
    return rows.map((item, index) => ({item, index, key: value(item)}))
      .sort((a, b) => {
        const aMissing = a.key === null || a.key === undefined, bMissing = b.key === null || b.key === undefined;
        if (aMissing || bMissing) return aMissing - bMissing || (a.index - b.index) * tie;
        const order = typeof a.key === 'string' ? a.key.localeCompare(b.key) : a.key - b.key;
        return order * direction || (a.index - b.index) * tie;
      })
      .map((entry) => entry.item);
  }

  function cells(values) {
    const row = node('div', 'pick-row pick-cols');
    for (const [className, text] of values) row.append(node('span', className, text));
    return row;
  }
  function headRow() {
    return cells([['', ''], ['n', '#'], ['th', ''], ['t', 'Title'], ['c', 'Channel'], ['v len', 'Length'],
      ['v views', 'Views'], ['v age', 'Uploaded'], ['s', 'Status'], ['w', '']]);
  }
  function row(item, index) {
    const line = node('div', 'pick-row' + (selected.has(item.video_id) ? ' on' : '') + (available(item) ? '' : ' off'));
    line.dataset.index = index;
    line.tabIndex = -1;
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.tabIndex = -1;
    box.checked = selected.has(item.video_id);
    box.disabled = busy || !available(item);
    box.setAttribute('aria-label', 'Select ' + item.title);
    const image = node('img', 'th');
    image.loading = 'lazy';
    image.referrerPolicy = 'no-referrer';
    image.alt = '';
    image.src = thumbUrl(item.video_id, 'mqdefault');
    const title = node('span', 't', item.title);
    title.title = item.title;
    let state;
    if (item.archived) {
      state = link('Saved', '/v/' + item.video_id);
      state.className = 's saved';
    } else {
      state = node('span', 's' + (item.queued ? ' queued' : item.unavailable ? ' bad' : ''),
        item.queued ? 'Queued' : item.unavailable || '');
    }
    const watch = link('YouTube ↗', watchUrl(item.video_id), true);
    watch.className = 'w';
    line.append(box, node('span', 'n', number.format(index + 1)), image, title, node('span', 'c', item.channel || ''),
      node('span', 'v len', duration(item.duration)), node('span', 'v views', item.views === null || item.views === undefined ? '' : compact.format(item.views)),
      node('span', 'v age', age(item.timestamp)), state, watch);
    return line;
  }

  function renderList() {
    shown = view();
    const pages = Math.max(1, Math.ceil(shown.length / PAGE));
    page = Math.max(0, Math.min(page, pages - 1));
    const list = el('items');
    list.classList.toggle('with-channel', listing.kind === 'playlist');
    const rows = document.createDocumentFragment();
    rows.append(headRow());
    shown.slice(page * PAGE, (page + 1) * PAGE).forEach((item, offset) => rows.append(row(item, page * PAGE + offset)));
    if (!shown.length) {
      rows.append(node('p', 'pick-empty', items.length ? 'Nothing here matches these filters.' : 'YouTube listed no videos here.'));
    }
    list.replaceChildren(rows);
    el('page').replaceChildren('page ', strong(number.format(page + 1)), ' of ', strong(number.format(pages)));
    el('prev').disabled = page === 0;
    el('next').disabled = page + 1 >= pages;
    el('prev').parentElement.hidden = pages === 1;
    const count = (test) => number.format(shown.filter(test).length);
    const parts = [strong(number.format(shown.length)), ' shown · ', strong(count(available)), ' can be chosen'];
    for (const [test, label] of [[(i) => i.archived, ' saved'], [(i) => i.queued && !i.archived, ' queued'],
      [(i) => i.unavailable && !i.archived && !i.queued, ' unavailable']]) {
      if (shown.some(test)) parts.push(' · ', strong(count(test)), label);
    }
    el('count').replaceChildren(...parts);
    paintBar();
  }

  function paintRows() {
    for (const line of el('items').querySelectorAll('.pick-row[data-index]')) {
      const item = shown[Number(line.dataset.index)];
      const on = selected.has(item.video_id);
      line.classList.toggle('on', on);
      const box = line.querySelector('input');
      box.checked = on;
      box.disabled = busy || !available(item);
    }
  }
  function paintBar() {
    const choosable = shown.filter(available).length;
    const visible = new Set(shown.map((item) => item.video_id));
    const elsewhere = [...selected].filter((id) => !visible.has(id)).length;
    el('selected-count').textContent = number.format(selected.size);
    el('offview').textContent = elsewhere ? ' · ' + number.format(elsewhere) + ' of them not shown here' : '';
    el('get-selected').textContent = selected.size ? 'Get ' + number.format(selected.size) + ' selected' : 'Get selected';
    el('get-selected').disabled = busy || !selected.size;
    el('get-shown').textContent = 'Get all shown (' + number.format(choosable) + ')';
    for (const id of ['get-shown', 'one-start', 'select-shown', 'select-first']) el(id).disabled = busy || !choosable;
    el('clear').disabled = busy || !selected.size;
    el('refresh').disabled = busy;
  }
  function changed() {
    saveSelection();
    paintRows();
    paintBar();
  }

  function toggle(index, extend) {
    const item = shown[index];
    if (busy || !item || !available(item)) return;
    const on = !selected.has(item.video_id);
    const [from, to] = extend && anchor >= 0 ? [Math.min(anchor, index), Math.max(anchor, index)] : [index, index];
    for (const other of shown.slice(from, to + 1)) {
      if (!available(other)) continue;
      if (on) selected.add(other.video_id); else selected.delete(other.video_id);
    }
    anchor = index;
    changed();
  }
  function move(delta) {
    if (!shown.length) return;
    const next = Math.max(0, Math.min(shown.length - 1, focus < 0 ? page * PAGE : focus + delta));
    if (Math.floor(next / PAGE) !== page) {
      page = Math.floor(next / PAGE);
      renderList();
    }
    focus = next;
    const line = el('items').querySelector('.pick-row[data-index="' + next + '"]');
    if (line) {
      line.focus({preventScroll: true});
      line.scrollIntoView({block: 'nearest'});
    }
  }

  function startOne() {
    const queue = shown.filter(available).map((item) => item.video_id);
    if (!queue.length) return;
    const first = queue.findIndex((id) => !selected.has(id));
    one = {queue, index: first < 0 ? 0 : first, byId: new Map(items.map((item) => [item.video_id, item]))};
    render();
    el('one').scrollIntoView({block: 'start'});
    el('one-choose').focus({preventScroll: true});
  }
  function fact(value, label) {
    const span = node('span');
    span.append(strong(value), label);
    return span;
  }
  function paintOne() {
    const total = one.queue.length, end = one.index >= total;
    const item = end ? null : one.byId.get(one.queue[one.index]);
    el('one-index').textContent = number.format(Math.min(one.index + 1, total));
    el('one-total').textContent = number.format(total);
    el('one-chosen').replaceChildren(' · ', strong(number.format(selected.size)), ' selected in total');
    const image = el('one-thumb');
    image.hidden = end;
    if (!end) image.src = thumbUrl(item.video_id, 'hqdefault');
    el('one-title').textContent = end ? 'That was the last one.' : item.title;
    const facts = [];
    if (end) {
      facts.push(fact(number.format(selected.size), ' selected. Get them now, or go back to the list to change the selection.'));
    } else {
      if (selected.has(item.video_id)) facts.push(fact('✓', ' selected'));
      if (item.duration !== null && item.duration !== undefined) facts.push(fact(duration(item.duration), ' long'));
      if (item.views !== null && item.views !== undefined) facts.push(fact(compact.format(item.views), ' views'));
      if (typeof item.timestamp === 'number') facts.push(fact(age(item.timestamp), ' ago'));
      if (listing.kind === 'playlist' && item.channel) facts.push(fact(item.channel, ''));
    }
    el('one-facts').replaceChildren(...facts);
    const choose = el('one-choose');
    choose.replaceChildren(end ? (selected.size ? 'Get ' + number.format(selected.size) + ' selected' : 'Nothing selected')
      : 'Choose ', ...(end ? [] : [node('kbd', '', 'y')]));
    choose.disabled = busy || (end && !selected.size);
    el('one-skip').hidden = end;
    el('one-back').disabled = one.index === 0;
    el('one-youtube').hidden = end;
    if (!end) el('one-youtube').href = watchUrl(item.video_id);
  }
  function decide(choose) {
    if (!one || one.index >= one.queue.length) return;
    const id = one.queue[one.index];
    if (choose) selected.add(id); else selected.delete(id);
    saveSelection();
    one.index++;
    paintOne();
    if (one.index >= one.queue.length) el('one-choose').focus({preventScroll: true});
  }
  function endOne() {
    one = null;
    render();
  }

  async function enqueue(ids) {
    if (busy || !ids.length) return;
    const hours = Math.floor((ids.length - 1) / DOWNLOADS_PER_HOUR);
    if (ids.length > CONFIRM_OVER && !confirm('Queue ' + number.format(ids.length) + ' videos?\n\nThey download one at a time'
        + (hours ? ', at most ' + DOWNLOADS_PER_HOUR + ' an hour, so this takes more than ' + hours + (hours === 1 ? ' hour' : ' hours') : '')
        + '. Videos already saved or queued are skipped.')) return;
    busy = true;
    if (one) paintOne(); else { paintRows(); paintBar(); }
    status('Adding ' + number.format(ids.length) + ' videos to the download queue…');
    try {
      const result = await request('/api/creators/' + listing.lookup_id + '/get', {video_ids: ids});
      for (const id of ids) selected.delete(id);
      saveSelection();
      status([strong(number.format(result.queued)), result.queued === 1 ? ' video queued' : ' videos queued',
        result.skipped ? [' · ', strong(number.format(result.skipped)), ' were already saved or queued'] : '',
        '. They download one at a time; you can leave this page.'].flat(), 'done');
      const queued = new Set(result.video_ids);
      for (const item of items) if (queued.has(item.video_id)) item.queued = true;
      try { items = (await request('/api/creators/' + listing.lookup_id)).items || items; } catch (_) { /* keep local flags */ }
      one = null;
    } catch (error) {
      status(error.message + ' Trying again is safe: videos already queued are skipped.', 'error');
    } finally {
      busy = false;
      render();
      el('status').scrollIntoView({block: 'nearest'});
    }
  }

  async function loadPlaylists() {
    if (listing.kind !== 'channel' || playlists || playlistsLoading) return;
    playlistsLoading = true;
    const current = generation;
    const line = el('playlists-status');
    line.className = '';
    line.textContent = 'Opening the playlists…';
    try {
      const result = await load(listing.url + '/playlists', false, (state) => line.replaceChildren(...progress(state)), current);
      if (!result || current !== generation) return;
      playlists = result;
      render();
    } catch (error) {
      if (current === generation) {
        line.className = 'pick-bad';
        line.textContent = error.message;
      }
    } finally {
      if (current === generation) playlistsLoading = false;
    }
  }
  function renderPlaylists() {
    const source = listing.kind === 'playlists' ? listing : playlists;
    const list = el('playlist-items');
    if (!source) {
      list.replaceChildren();
      return;
    }
    const line = el('playlists-status');
    line.className = '';
    const refresh = node('button', 'btn', 'Refresh playlists');
    refresh.type = 'button';
    refresh.onclick = async () => {
      refresh.disabled = true;
      const current = generation;
      try {
        const result = await load(source.url, true, (state) => line.replaceChildren(...progress(state)), current);
        if (!result || current !== generation) return;
        if (listing.kind === 'playlists') { listing = result; items = result.items; } else playlists = result;
        render();
        if (result.error) { line.className = 'pick-bad'; line.append(' ', result.error); }
      } catch (error) {
        line.className = 'pick-bad';
        line.textContent = error.message;
      }
    };
    line.replaceChildren(strong(number.format(source.items.length)),
      source.items.length === 1 ? ' playlist' : ' playlists',
      source.items.length ? ' · open one to choose from its videos · ' : ' · ', ...listed(source.fetched_at), ' ', refresh);
    if (source.warning) line.append(' ', node('span', 'pick-bad', source.warning));
    const rows = document.createDocumentFragment();
    for (const item of source.items) {
      const playlistUrl = 'https://www.youtube.com/playlist?list=' + item.playlist_id;
      const row = node('div', 'pick-row pl');
      const image = item.thumb_id ? node('img', 'th') : node('span', 'th ph', 'no image');
      if (item.thumb_id) {
        image.loading = 'lazy';
        image.referrerPolicy = 'no-referrer';
        image.alt = '';
        image.src = thumbUrl(item.thumb_id, 'mqdefault');
      }
      const title = link(item.title, browseHref(playlistUrl));
      title.className = 't';
      title.title = item.title;
      const open = link('choose videos →', browseHref(playlistUrl));
      open.className = 'w';
      const watch = link('YouTube ↗', playlistUrl, true);
      watch.className = 'w';
      row.append(image, title, open, watch);
      rows.append(row);
    }
    list.replaceChildren(rows);
  }

  el('form').addEventListener('submit', (event) => { event.preventDefault(); browse(); });
  el('refresh').onclick = () => browse(true);
  for (const id of ['filter', 'length', 'age', 'sort', 'hide']) {
    el(id).addEventListener(id === 'filter' ? 'input' : 'change', () => {
      page = 0; anchor = -1; focus = -1;
      render();
    });
  }
  el('items').addEventListener('mousedown', (event) => { if (event.shiftKey) event.preventDefault(); });
  el('items').addEventListener('click', (event) => {
    if (event.target.closest('a')) return;
    const line = event.target.closest('.pick-row[data-index]');
    if (!line) return;
    event.preventDefault();
    focus = Number(line.dataset.index);
    line.focus({preventScroll: true});
    toggle(focus, event.shiftKey);
  });
  el('get-selected').onclick = () => enqueue([...selected]);
  el('get-shown').onclick = () => enqueue(shown.filter(available).map((item) => item.video_id));
  el('select-shown').onclick = () => { shown.filter(available).forEach((item) => selected.add(item.video_id)); changed(); };
  el('select-first').onclick = () => {
    const count = Math.max(1, Math.floor(Number(el('first').value) || 0));
    shown.filter(available).slice(0, count).forEach((item) => selected.add(item.video_id));
    changed();
  };
  el('clear').onclick = () => { selected.clear(); changed(); };
  el('prev').onclick = () => { page--; focus = -1; renderList(); el('count').scrollIntoView({block: 'start'}); };
  el('next').onclick = () => { page++; focus = -1; renderList(); el('count').scrollIntoView({block: 'start'}); };
  el('one-start').onclick = startOne;
  el('one-choose').onclick = () => {
    if (one && one.index >= one.queue.length) enqueue([...selected]);
    else decide(true);
  };
  el('one-skip').onclick = () => decide(false);
  el('one-back').onclick = () => { if (one && one.index > 0) { one.index--; paintOne(); } };
  el('one-done').onclick = endOne;

  document.addEventListener('keydown', (event) => {
    if (!listing || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.target.closest('input:not([type=checkbox]), select, textarea')) return;
    const key = event.key;
    if (one && !el('one').hidden) {
      if (key === 'y' || key === 'Y') decide(true);
      else if (key === 'n' || key === 'N' || key === 'ArrowRight') decide(false);
      else if (key === 'b' || key === 'B' || key === 'ArrowLeft') el('one-back').click();
      else if (key === 'Escape') endOne();
      else return;
      event.preventDefault();
      return;
    }
    if (el('videos').hidden) return;
    const onControl = event.target.closest('button, a');
    if (key === 'j' || key === 'ArrowDown') move(1);
    else if (key === 'k' || key === 'ArrowUp') move(-1);
    else if ((key === 'x' || key === 'X' || (key === ' ' && !onControl)) && focus >= 0) toggle(focus, event.shiftKey);
    else if (key === 'o' && focus >= 0 && shown[focus]) window.open(watchUrl(shown[focus].video_id), '_blank', 'noopener');
    else return;
    event.preventDefault();
  });

  if (el('source').value.trim()) browse();
})();
