const {vid, site} = JSON.parse(document.getElementById('page-data').textContent);
const STEP_NAMES = {download: 'downloading', shots: 'making shots', audio: 'making MP3 audio', queued: 'waiting in the queue'};
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
async function watchJob(jobId, status) {
  for (;;) {
    const res = await fetch('/api/job/' + encodeURIComponent(jobId), {cache: 'no-store'});
    const j = await res.json();
    if (!res.ok) throw new Error(j.error || res.statusText);
    if (j.status === 'done') {
      status.className = 'done';
      status.textContent = (j.message || 'done') + ' — reloading';
      location.reload();
      return true;
    }
    if (j.status === 'error') throw new Error(j.error || 'failed');
    const pct = j.progress != null ? Math.floor(j.progress * 100) + '% · ' : '';
    status.textContent = (STEP_NAMES[j.phase] || j.phase) + ' · ' + pct + (j.message || '');
    await new Promise((r) => setTimeout(r, 1000));
  }
}
const pngBtn = document.getElementById('reget-png');
const videoBtn = document.getElementById('reget-video');
const makeShotsBtn = document.getElementById('make-shots');
async function reget(kind, status) {
  const regetShots = Boolean(pngBtn);
  const promptText = kind === 'video'
    ? (regetShots
      ? 'Download this video from ' + site + ' again and rebuild its shots and framesheet?'
      : 'Download this video from ' + site + ' again?')
    : (kind === 'make-shots'
      ? 'Make shot images and framesheet from the local video? Videos over 30 minutes can take several minutes.'
      : 'Rebuild the shot images and framesheet from the local video?');
  if (!confirm(promptText)) return;
  const buttons = [pngBtn, videoBtn, makeShotsBtn].filter(Boolean);
  buttons.forEach((b) => { b.disabled = true; });
  status.className = '';
  status.textContent = 'starting…';
  const body = kind === 'video'
    ? (regetShots ? {url: vid, force_video: true, force_shots: true} : {url: vid, force_video: true})
    : {url: vid, force_shots: true};
  try {
    const data = await post('/api/get', body);
    await watchJob(data.job_id, status);
  } catch (err) {
    status.className = 'error';
    status.textContent = err.message;
    buttons.forEach((b) => { b.disabled = false; });
  }
}
if (pngBtn) pngBtn.onclick = () => reget('png', document.getElementById('reget-status'));
if (videoBtn) videoBtn.onclick = () => reget('video', document.getElementById('reget-status'));
if (makeShotsBtn) makeShotsBtn.onclick = () => reget('make-shots', document.getElementById('shots-status'));
const dumpBtn = document.getElementById('dump-audio');
if (dumpBtn) dumpBtn.onclick = async () => {
  dumpBtn.disabled = true;
  const status = document.getElementById('audio-status');
  status.className = '';
  status.textContent = 'starting…';
  try {
    const data = await post('/api/get', {url: vid});
    if (data.status === 'complete') { location.reload(); return; }
    await watchJob(data.job_id, status);
  } catch (err) {
    status.className = 'error';
    status.textContent = err.message;
    dumpBtn.disabled = false;
  }
};
const openBtn = document.getElementById('open-mp3-folder');
if (openBtn) openBtn.onclick = async () => {
  openBtn.disabled = true;
  try {
    const res = await fetch('/api/open-folder', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({video_id: vid}),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
  } catch (err) {
    alert(err.message);
  }
  openBtn.disabled = false;
};

const transcribeDialog = document.getElementById('transcribe-dialog');
const transcribeProgress = document.getElementById('transcript-progress');
const transcribeValue = document.getElementById('transcript-progress-value');
const transcribeLabel = document.getElementById('transcript-progress-label');
const transcribeFill = document.getElementById('transcript-progress-fill');
const transcriptReady = document.getElementById('transcript-panel').dataset.ready === 'true';
const whisperFields = document.getElementById('whisper-fields');
const configuredBtn = document.getElementById('configured-transcribe');
const defaultBtn = document.getElementById('default-transcribe');

function selectedEngine() {
  const checked = transcribeDialog.querySelector('input[name="transcribe-engine"]:checked');
  return checked ? checked.value : 'whisper';
}
function syncTranscribeDialog() {
  const isMultitalker = selectedEngine() === 'multitalker';
  if (whisperFields) whisperFields.hidden = isMultitalker;
  if (configuredBtn) configuredBtn.hidden = isMultitalker;
  if (defaultBtn) {
    if (isMultitalker) {
      defaultBtn.textContent = transcriptReady
        ? 'Replace with multi-speaker'
        : 'Yes — transcribe multi-speaker';
    } else {
      defaultBtn.textContent = transcriptReady
        ? 'Replace with defaults'
        : 'Yes — transcribe with defaults';
    }
  }
}
transcribeDialog.querySelectorAll('input[name="transcribe-engine"]').forEach(radio => {
  radio.addEventListener('change', syncTranscribeDialog);
});
syncTranscribeDialog();

document.getElementById('open-transcribe').onclick = () => {
  syncTranscribeDialog();
  transcribeDialog.showModal();
};
document.getElementById('cancel-transcribe').onclick = () => transcribeDialog.close();
defaultBtn.onclick = () => {
  if (selectedEngine() === 'multitalker') {
    startTranscription({engine: 'multitalker'});
  } else {
    startTranscription({
      engine: 'whisper', model: 'large-v3', language: '', beam_size: 5, vad_filter: true,
    });
  }
};
configuredBtn.onclick = () => startTranscription({
  engine: 'whisper',
  model: document.getElementById('whisper-model').value,
  language: document.getElementById('whisper-language').value.trim(),
  beam_size: Number(document.getElementById('whisper-beam').value),
  vad_filter: document.getElementById('whisper-vad').checked,
});

async function startTranscription(config) {
  transcribeDialog.close();
  document.getElementById('open-transcribe').disabled = true;
  transcribeProgress.hidden = false;
  transcribeProgress.className = 'transcript-progress';
  setTranscribeProgress(
    0,
    config.engine === 'multitalker'
      ? 'queueing multi-speaker transcription…'
      : 'queueing local Whisper…'
  );
  try {
    const res = await fetch('/api/transcribe', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({video_id: vid, config, force: transcriptReady}),
    });
    const job = await res.json();
    if (!res.ok) throw new Error(job.error || res.statusText);
    for (;;) {
      const jres = await fetch('/api/work/' + job.job_id);
      const current = await jres.json();
      if (!jres.ok) throw new Error(current.error || jres.statusText);
      setTranscribeProgress(
        current.progress || 0,
        current.message || current.status
      );
      if (current.status === 'done') {
        setTranscribeProgress(1, 'transcript ready — reloading');
        setTimeout(() => location.reload(), 500);
        return;
      }
      if (current.status === 'error') {
        throw new Error(current.error || 'transcription failed');
      }
      await new Promise(resolve => setTimeout(resolve, 900));
    }
  } catch (err) {
    transcribeProgress.classList.add('error');
    transcribeLabel.textContent = err.message;
    document.getElementById('open-transcribe').disabled = false;
  }
}

function setTranscribeProgress(value, label) {
  const pct = Math.max(0, Math.min(100, Math.round(Number(value || 0) * 100)));
  transcribeValue.textContent = pct + '%';
  transcribeLabel.textContent = label;
  transcribeFill.style.width = pct + '%';
}

function html(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function clock(t) {
  t = Math.max(0, Math.floor(Number(t) || 0));
  const h = Math.floor(t / 3600);
  const m = Math.floor(t / 60) % 60;
  const s = t % 60;
  return h ? h + ':' + String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0')
    : m + ':' + String(s).padStart(2, '0');
}
function clockPrecise(t) {
  t = Math.max(0, Number(t) || 0);
  const whole = Math.floor(t);
  const tenths = Math.floor((t - whole) * 10);
  return clock(whole) + '.' + tenths;
}
function clipText(text, maxLen) {
  const s = String(text || '').trim();
  return s.length > maxLen ? s.slice(0, maxLen - 1).trimEnd() + '…' : s;
}

const SPEAKER_PALETTE = [
  {fg: '#7cc4ff', bg: 'rgba(124,196,255,0.22)', bgHi: 'rgba(124,196,255,0.44)'},
  {fg: '#ffb84d', bg: 'rgba(255,184,77,0.22)',  bgHi: 'rgba(255,184,77,0.44)'},
  {fg: '#7ee787', bg: 'rgba(126,231,135,0.22)', bgHi: 'rgba(126,231,135,0.44)'},
  {fg: '#d2a8ff', bg: 'rgba(210,168,255,0.22)', bgHi: 'rgba(210,168,255,0.44)'},
  {fg: '#ff85a2', bg: 'rgba(255,133,162,0.22)', bgHi: 'rgba(255,133,162,0.44)'},
  {fg: '#66e3d8', bg: 'rgba(102,227,216,0.22)', bgHi: 'rgba(102,227,216,0.44)'},
  {fg: '#f5e050', bg: 'rgba(245,224,80,0.22)',  bgHi: 'rgba(245,224,80,0.44)'},
  {fg: '#ffa07a', bg: 'rgba(255,160,122,0.22)', bgHi: 'rgba(255,160,122,0.44)'},
];
const UNASSIGNED_COLOR = {fg: '#a9d6ff', bg: 'rgba(169,214,255,0.16)', bgHi: 'rgba(169,214,255,0.36)'};

let transcript = null;
let model = null;
let rowByIndex = new Map();
let activeKey = '';
let activeWordSpans = [];
let hoveredIndices = null;
let hoveredKey = '';

// Swimlane state
let lanesWindow = Number(localStorage.getItem('ytarchive.transcript.lanesWindow')) || 30;
let lanesHidden = localStorage.getItem('ytarchive.transcript.lanesHidden') === 'true';
let lanesFollow = true;
let lanesWinStart = 0;
let renderedWinStart = -1;
let renderedWinDur = -1;
let playheadEls = [];
let blockElsByIndex = new Map();
let rafId = 0;

function buildOverlapModel(data) {
  const segments = (data && data.segments) || [];
  let maxEnd = Number(data && data.duration) || 0;
  const speakers = [];
  const speakerCounts = new Map();
  const byIndex = new Map();
  const infoByIndex = new Map();
  for (const seg of segments) {
    if (seg.end > maxEnd) maxEnd = seg.end;
    byIndex.set(seg.index, seg);
    infoByIndex.set(seg.index, {
      partners: [],
      overlapsPrev: false,
      chipsByWord: new Map(),
      endChips: [],
    });
    const spk = seg.speaker || '';
    if (spk) {
      if (!speakerCounts.has(spk)) speakers.push(spk);
      speakerCounts.set(spk, (speakerCounts.get(spk) || 0) + 1);
    }
  }
  const colorMap = new Map();
  speakers.forEach((spk, i) => {
    colorMap.set(spk, SPEAKER_PALETTE[i % SPEAKER_PALETTE.length]);
  });
  const colorOf = spk => (spk && colorMap.get(spk)) || UNASSIGNED_COLOR;

  const sorted = [...segments].sort((a, b) => (a.start - b.start) || (a.end - b.end));
  const active = [];
  const rawOverlaps = [];

  function attachChip(host, guest, ovStart, ovEnd) {
    const info = infoByIndex.get(host.index);
    const chip = {other: guest, color: colorOf(guest.speaker).fg, ovStart, ovEnd};
    const words = host.words || [];
    if (!words.length) {
      info.endChips.push(chip);
      return;
    }
    let anchor = -1;
    for (let i = 0; i < words.length; i++) {
      const wStart = words[i].start != null ? Number(words[i].start) : Number(host.start);
      const wEnd = words[i].end != null ? Number(words[i].end) : wStart;
      if (wStart < ovEnd && wEnd > ovStart) anchor = i;
    }
    if (anchor === -1) {
      for (let i = 0; i < words.length; i++) {
        const wStart = words[i].start != null ? Number(words[i].start) : Number(host.start);
        if (wStart <= guest.start) anchor = i;
        else break;
      }
      if (anchor === -1) anchor = 0;
    }
    if (!info.chipsByWord.has(anchor)) info.chipsByWord.set(anchor, []);
    info.chipsByWord.get(anchor).push(chip);
  }

  for (const seg of sorted) {
    for (let i = active.length - 1; i >= 0; i--) {
      if (active[i].end <= seg.start + 0.05) active.splice(i, 1);
    }
    for (const prev of active) {
      if ((prev.speaker || '') === (seg.speaker || '')) continue;
      const ovStart = Math.max(prev.start, seg.start);
      const ovEnd = Math.min(prev.end, seg.end);
      if (ovEnd - ovStart < 0.05) continue;
      infoByIndex.get(prev.index).partners.push({other: seg, ovStart, ovEnd});
      infoByIndex.get(seg.index).partners.push({other: prev, ovStart, ovEnd});
      infoByIndex.get(seg.index).overlapsPrev = true;
      rawOverlaps.push({
        start: ovStart,
        end: ovEnd,
        speakers: [prev.speaker || 'Unassigned', seg.speaker || 'Unassigned'],
        indices: [prev.index, seg.index],
      });
      if (seg.start >= prev.start + 0.15) {
        attachChip(prev, seg, ovStart, ovEnd);
      } else if (prev.end > seg.end + 0.8) {
        attachChip(prev, seg, ovStart, ovEnd);
      } else if (seg.end > prev.end + 0.8) {
        attachChip(seg, prev, ovStart, ovEnd);
      }
    }
    active.push(seg);
  }

  rawOverlaps.sort((a, b) => a.start - b.start || a.end - b.end);
  const overlapSpans = [];
  for (const ov of rawOverlaps) {
    const last = overlapSpans[overlapSpans.length - 1];
    if (last && ov.start <= last.end + 0.15) {
      if (ov.end > last.end) last.end = ov.end;
      ov.speakers.forEach(s => last.speakers.add(s));
      ov.indices.forEach(idx => last.indices.add(idx));
    } else {
      overlapSpans.push({
        start: ov.start,
        end: ov.end,
        speakers: new Set(ov.speakers),
        indices: new Set(ov.indices),
      });
    }
  }

  const bySpeaker = new Map();
  for (const spk of speakers) bySpeaker.set(spk, []);
  for (const seg of sorted) {
    if (seg.speaker && bySpeaker.has(seg.speaker)) bySpeaker.get(seg.speaker).push(seg);
  }

  return {
    duration: Math.max(1, maxEnd),
    speakers,
    speakerCounts,
    colorOf,
    byIndex,
    infoByIndex,
    overlapSpans,
    sorted,
    bySpeaker,
  };
}

function modelLabel(data) {
  if (!data) return '';
  if (data.engine === 'multitalker-parakeet') return 'Nemotron-3 + Parakeet Ultra';
  const probability = Math.round((data.language_probability || 0) * 100);
  const lang = (data.language || 'unknown language') + (probability ? ' ' + probability + '%' : '');
  return lang + ' · ' + (data.model || 'Whisper');
}

async function loadTranscript() {
  const res = await fetch('/api/transcript/' + encodeURIComponent(vid));
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  setTranscriptData(data);
}

function setTranscriptData(data) {
  transcript = data;
  model = buildOverlapModel(data);
  paintSpeakerOptions();
  paintFilterOptions();
  paintTranscript();
  setupLanes();
  syncTranscript(true);
}

function paintTranscriptSummary(shownCount) {
  const el = document.getElementById('transcript-meta');
  if (!el || !transcript || !model) return;
  const parts = [];
  if (shownCount != null) {
    parts.push(shownCount + ' of ' + transcript.segments.length + ' segments shown');
  } else {
    parts.push(transcript.segments.length + ' spoken segments');
  }
  if (model.speakers.length >= 2) {
    parts.push(model.speakers.length + ' speakers');
    parts.push(
      model.overlapSpans.length + ' overlap' + (model.overlapSpans.length === 1 ? '' : 's')
    );
  }
  parts.push(modelLabel(transcript));
  el.textContent = parts.join(' · ');
}

function paintFilterOptions() {
  const wrap = document.getElementById('transcript-filter-wrap');
  const select = document.getElementById('transcript-filter');
  if (!wrap || !select || !model) return;
  const prev = select.value;
  const multi = model.speakers.length >= 2;
  wrap.hidden = !multi;
  if (!multi) {
    select.value = 'all';
    return;
  }
  const opts = ['<option value="all">all segments</option>'];
  if (model.overlapSpans.length) {
    opts.push(
      '<option value="overlaps">overlaps only (' + model.overlapSpans.length + ')</option>'
    );
  }
  for (const spk of model.speakers) {
    const count = model.speakerCounts.get(spk) || 0;
    opts.push(
      '<option value="spk:' + html(spk) + '">' + html(spk) + ' (' + count + ')</option>'
    );
  }
  select.innerHTML = opts.join('');
  if ([...select.options].some(o => o.value === prev)) select.value = prev;
  else select.value = 'all';
}

function chipHtml(chip) {
  const spk = chip.other.speaker || 'Other';
  const full = spk + ' (' + clockPrecise(chip.other.start) + '–'
    + clockPrecise(chip.other.end) + '): ' + chip.other.text;
  return ' <span class="ov-chip" data-target="' + chip.other.index
    + '" style="--ov-color:' + chip.color + '" title="' + html(full) + '">'
    + '∥ <strong>' + html(spk) + '</strong>: “' + html(clipText(chip.other.text, 44)) + '”</span>';
}

function segmentHtml(segment, matched, hidden) {
  const info = model.infoByIndex.get(segment.index);
  const color = model.colorOf(segment.speaker);
  const partners = info ? info.partners : [];
  const partnerIds = partners.length
    ? [...new Set(partners.map(p => p.other.index))].join(',')
    : '';
  let body = '';
  const words = segment.words || [];
  if (words.length) {
    body = words.map((word, wi) => {
      const gap = wi && !/^[,.;:!?)]/.test(word.word) ? ' ' : '';
      const wStart = Number(word.start != null ? word.start : segment.start);
      const wEnd = Number(word.end != null ? word.end : wStart);
      let ovPartner = null;
      let ovIds = [];
      if (partners.length) {
        for (const p of partners) {
          if (wStart < p.ovEnd && wEnd > p.ovStart) {
            if (!ovPartner) ovPartner = p;
            ovIds.push(p.other.index);
          }
        }
      }
      let span = '<span class="word' + (ovPartner ? ' word-ov' : '') + '"'
        + ' data-t="' + wStart + '" data-e="' + wEnd + '"';
      if (ovPartner) {
        const pColor = model.colorOf(ovPartner.other.speaker).fg;
        const pTitle = 'Overlaps ' + (ovPartner.other.speaker || 'other speaker')
          + ' (' + clockPrecise(ovPartner.other.start) + '–'
          + clockPrecise(ovPartner.other.end) + '): “' + ovPartner.other.text + '”';
        span += ' data-ov="' + ovIds.join(',') + '" style="--ov-color:' + pColor
          + '" title="' + html(pTitle) + '"';
      }
      span += '>' + html(word.word) + '</span>';
      const chips = info && info.chipsByWord.get(wi);
      if (chips && chips.length) {
        span += chips.map(chipHtml).join('');
      }
      return gap + span;
    }).join('');
  } else {
    body = html(segment.text);
    if (info && info.endChips.length) {
      body += info.endChips.map(chipHtml).join('');
    }
  }
  const ovMark = partners.length
    ? '<span class="ov-mark" title="' + html(
        'Simultaneous with ' + [...new Set(partners.map(p => p.other.speaker || 'other'))].join(', ')
      ) + '">∥</span>'
    : '';
  const cls = 'segment'
    + (matched ? ' match' : '')
    + (info && info.overlapsPrev ? ' ov-child' : '');
  return '<div class="' + cls + '"'
    + (hidden ? ' hidden' : '')
    + ' style="--spk-color:' + color.fg + '"'
    + ' data-index="' + segment.index + '" data-start="' + Number(segment.start)
    + '" data-end="' + Number(segment.end) + '"'
    + (partnerIds ? ' data-partners="' + partnerIds + '"' : '')
    + '><span class="segment-time">' + clock(segment.start) + ovMark + '</span>'
    + '<button type="button" class="segment-speaker" title="assign or group speaker: '
    + html(segment.speaker || 'Unassigned') + '">' + html(segment.speaker || 'Unassigned') + '</button>'
    + '<div class="segment-text">' + body + '</div></div>';
}

function paintTranscript() {
  const box = document.getElementById('caption-box');
  if (!box || !transcript || !model) return;
  const query = (document.getElementById('transcript-search')?.value || '').trim().toLowerCase();
  const filter = document.getElementById('transcript-filter')?.value || 'all';
  let shown = 0;
  const isFiltered = Boolean(query) || filter !== 'all';
  box.innerHTML = transcript.segments.map(segment => {
    const info = model.infoByIndex.get(segment.index);
    let passFilter = true;
    if (filter === 'overlaps') {
      passFilter = Boolean(info && info.partners.length);
    } else if (filter.startsWith('spk:')) {
      passFilter = (segment.speaker || '') === filter.slice(4);
    }
    const passQuery = !query
      || segment.text.toLowerCase().includes(query)
      || (segment.speaker || '').toLowerCase().includes(query);
    const visible = passFilter && passQuery;
    if (visible) shown += 1;
    return segmentHtml(segment, Boolean(query && visible), !visible);
  }).join('') || '<div class="transcript-empty">No spoken words found.</div>';

  rowByIndex = new Map();
  box.querySelectorAll('.segment').forEach(row => {
    rowByIndex.set(Number(row.dataset.index), row);
  });
  paintTranscriptSummary(isFiltered ? shown : null);
  updateLaneFilterHighlights();
  activeKey = '';
  syncTranscript(true);
}

function clearHoverLink() {
  if (!hoveredIndices) return;
  for (const idx of hoveredIndices) {
    const row = rowByIndex.get(idx);
    if (row) {
      row.classList.remove('ov-linked');
      row.querySelectorAll('.word-ov-hi').forEach(w => w.classList.remove('word-ov-hi'));
    }
    const blk = blockElsByIndex.get(idx);
    if (blk) blk.classList.remove('ov-linked');
  }
  hoveredIndices = null;
  hoveredKey = '';
  paintLanesStatus();
}

function setHoverLink(indices, primaryIdx, highlightPrimaryRow) {
  if (!indices || !indices.size) {
    clearHoverLink();
    return;
  }
  const key = [...indices].sort((a, b) => a - b).join(',') + ':' + primaryIdx + ':' + Boolean(highlightPrimaryRow);
  if (key === hoveredKey) return;
  clearHoverLink();
  hoveredIndices = new Set(indices);
  hoveredKey = key;
  for (const idx of hoveredIndices) {
    const row = rowByIndex.get(idx);
    if (row) {
      if (highlightPrimaryRow || idx !== primaryIdx) row.classList.add('ov-linked');
      row.querySelectorAll('.word-ov').forEach(w => {
        const ovs = (w.dataset.ov || '').split(',').map(Number);
        if (ovs.some(id => hoveredIndices.has(id))) w.classList.add('word-ov-hi');
      });
    }
    const blk = blockElsByIndex.get(idx);
    if (blk) blk.classList.add('ov-linked');
  }
  if (primaryIdx != null && model) {
    const seg = model.byIndex.get(primaryIdx);
    const status = document.getElementById('lanes-status');
    if (seg && status) {
      const spk = seg.speaker || 'Unassigned';
      const c = model.colorOf(seg.speaker).fg;
      status.innerHTML = '<strong style="color:' + c + '">' + html(spk) + '</strong> '
        + '<span class="num">' + clockPrecise(seg.start) + '–' + clockPrecise(seg.end) + '</span> · '
        + html(seg.text);
    }
  }
}

function seekAndPlay(time) {
  const playerVideo = document.querySelector('#ytp video');
  if (!playerVideo) return;
  playerVideo.currentTime = Math.max(0, Number(time) || 0);
  playerVideo.play().catch(() => {});
}

// Delegated transcript list interactions
const captionBox = document.getElementById('caption-box');
if (captionBox) {
  captionBox.addEventListener('click', async event => {
    const chip = event.target.closest('.ov-chip');
    if (chip && model) {
      event.stopPropagation();
      const targetIdx = Number(chip.dataset.target);
      const target = model.byIndex.get(targetIdx);
      if (target) {
        seekAndPlay(target.start);
        const row = rowByIndex.get(targetIdx);
        if (row && !row.hidden) row.scrollIntoView({block: 'nearest'});
      }
      return;
    }
    const spkBtn = event.target.closest('.segment-speaker');
    if (spkBtn) {
      event.stopPropagation();
      const row = spkBtn.closest('.segment');
      const index = Number(row.dataset.index);
      const segment = model && model.byIndex.get(index);
      const current = segment ? segment.speaker || '' : '';
      const speaker = prompt(
        'Speaker name. Use the same name on other segments to group them.',
        current
      );
      if (speaker == null) return;
      await changeSegmentSpeaker(index, speaker);
      return;
    }
    const row = event.target.closest('.segment');
    if (!row) return;
    const word = event.target.closest('.word');
    const time = word ? Number(word.dataset.t) : Number(row.dataset.start);
    seekAndPlay(time);
  });

  captionBox.addEventListener('mouseover', event => {
    const chip = event.target.closest('.ov-chip');
    if (chip && model) {
      const targetIdx = Number(chip.dataset.target);
      const row = chip.closest('.segment');
      const hostIdx = row ? Number(row.dataset.index) : null;
      const set = new Set([targetIdx]);
      if (hostIdx != null) set.add(hostIdx);
      setHoverLink(set, targetIdx);
      return;
    }
    const row = event.target.closest('.segment');
    if (!row || !row.dataset.partners) {
      clearHoverLink();
      return;
    }
    const idx = Number(row.dataset.index);
    const set = new Set([idx]);
    row.dataset.partners.split(',').forEach(s => set.add(Number(s)));
    setHoverLink(set, idx);
  });

  captionBox.addEventListener('mouseleave', clearHoverLink);
}

async function changeSegmentSpeaker(index, speaker) {
  const res = await fetch('/api/transcript/speaker', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({video_id: vid, segment_index: index, speaker}),
  });
  const data = await res.json();
  if (!res.ok) return alert(data.error || res.statusText);
  setTranscriptData(data);
}

function paintSpeakerOptions() {
  const select = document.getElementById('speaker-old');
  if (!select || !transcript) return;
  const speakers = [...new Set(transcript.segments.map(s => s.speaker).filter(Boolean))].sort();
  select.innerHTML = '<option value="">choose speaker</option>'
    + speakers.map(s => '<option value="' + html(s) + '">' + html(s) + '</option>').join('');
}

const renameSpeaker = document.getElementById('rename-speaker');
if (renameSpeaker) renameSpeaker.onclick = async () => {
  const oldName = document.getElementById('speaker-old').value;
  const newName = document.getElementById('speaker-new').value.trim();
  if (!oldName || !newName) return;
  const res = await fetch('/api/transcript/rename-speaker', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({video_id: vid, old: oldName, new: newName}),
  });
  const data = await res.json();
  if (!res.ok) return alert(data.error || res.statusText);
  document.getElementById('speaker-new').value = '';
  setTranscriptData(data);
};

// ---------------- Speaker Swimlanes & Minimap ----------------
function clampWinStart(start) {
  if (!model) return 0;
  return Math.max(0, Math.min(start, Math.max(0, model.duration - lanesWindow)));
}

function setupLanes() {
  const panel = document.getElementById('speaker-lanes');
  const toggleBtn = document.getElementById('toggle-lanes');
  const winSel = document.getElementById('lanes-window');
  const followBtn = document.getElementById('lanes-follow');
  if (!panel || !toggleBtn || !model) return;
  const multi = model.speakers.length >= 2;
  toggleBtn.hidden = !multi;
  if (!multi) {
    panel.hidden = true;
    return;
  }
  panel.hidden = lanesHidden;
  toggleBtn.textContent = lanesHidden ? 'Show timeline' : 'Hide timeline';
  if (winSel) winSel.value = String(lanesWindow);
  if (followBtn) followBtn.setAttribute('aria-pressed', String(lanesFollow));
  const prevBtn = document.getElementById('prev-overlap');
  const nextBtn = document.getElementById('next-overlap');
  const hasOverlaps = model.overlapSpans.length > 0;
  if (prevBtn) prevBtn.disabled = !hasOverlaps;
  if (nextBtn) nextBtn.disabled = !hasOverlaps;
  renderedWinStart = -1;
  renderedWinDur = -1;
  if (!lanesHidden) {
    drawMinimap();
    renderLanesWindow(true);
    paintLanesStatus();
  }
}

function updateLaneFilterHighlights() {
  const filter = document.getElementById('transcript-filter')?.value || 'all';
  document.querySelectorAll('.lane-label').forEach(lbl => {
    lbl.classList.toggle('filtered', filter === 'spk:' + lbl.dataset.speaker);
  });
}

function drawMinimap() {
  const canvas = document.getElementById('lanes-minimap');
  if (!canvas || !model || lanesHidden) return;
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(200, Math.round(rect.width * (window.devicePixelRatio || 1)));
  const height = Math.round(22 * (window.devicePixelRatio || 1));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.fillStyle = '#050505';
  ctx.fillRect(0, 0, width, height);

  const dur = model.duration;
  const nSpk = model.speakers.length;
  const rowH = height / Math.max(1, nSpk);
  model.speakers.forEach((spk, i) => {
    const segs = model.bySpeaker.get(spk) || [];
    ctx.fillStyle = model.colorOf(spk).fg;
    const y = Math.floor(i * rowH) + 1;
    const h = Math.max(2, Math.floor(rowH) - 2);
    for (const seg of segs) {
      const x0 = (seg.start / dur) * width;
      const x1 = (seg.end / dur) * width;
      ctx.fillRect(x0, y, Math.max(1.5, x1 - x0), h);
    }
  });
  ctx.fillStyle = '#ffd240';
  for (const ov of model.overlapSpans) {
    const x0 = (ov.start / dur) * width;
    const w = Math.max(2, ((ov.end - ov.start) / dur) * width);
    ctx.fillRect(x0, 0, w, Math.max(3, Math.round(height * 0.22)));
    ctx.fillRect(x0, height - Math.max(3, Math.round(height * 0.22)), w, Math.max(3, Math.round(height * 0.22)));
  }
}

function renderLanesWindow(force) {
  if (!model || lanesHidden || model.speakers.length < 2) return;
  const winStart = clampWinStart(lanesWinStart);
  const winDur = lanesWindow;
  if (!force && Math.abs(winStart - renderedWinStart) < 0.001 && winDur === renderedWinDur) {
    return;
  }
  lanesWinStart = winStart;
  renderedWinStart = winStart;
  renderedWinDur = winDur;
  const winEnd = winStart + winDur;

  const ruler = document.getElementById('lanes-ruler');
  const body = document.getElementById('lanes-body');
  if (!ruler || !body) return;

  const step = winDur <= 15 ? 3 : winDur <= 30 ? 5 : 10;
  const firstTick = Math.ceil(winStart / step) * step;
  const ticks = [];
  for (let t = firstTick; t <= winEnd; t += step) {
    const pct = ((t - winStart) / winDur) * 100;
    ticks.push(
      '<span class="ruler-tick" style="left:' + pct.toFixed(3) + '%">' + clock(t) + '</span>'
    );
  }
  ruler.innerHTML = ticks.join('') + '<div class="lane-playhead"></div>';

  const visibleOverlaps = model.overlapSpans.filter(ov => ov.end > winStart && ov.start < winEnd);
  const ovBandsHtml = visibleOverlaps.map(ov => {
    const left = ((Math.max(ov.start, winStart) - winStart) / winDur) * 100;
    const right = ((Math.min(ov.end, winEnd) - winStart) / winDur) * 100;
    return '<div class="lane-ov-band" style="left:' + left.toFixed(3)
      + '%;width:' + Math.max(0.25, right - left).toFixed(3) + '%"></div>';
  }).join('');

  const filter = document.getElementById('transcript-filter')?.value || 'all';
  const rowsHtml = model.speakers.map(spk => {
    const c = model.colorOf(spk);
    const segs = model.bySpeaker.get(spk) || [];
    const blocks = [];
    for (const seg of segs) {
      if (seg.end <= winStart) continue;
      if (seg.start >= winEnd) break;
      const s0 = Math.max(seg.start, winStart);
      const s1 = Math.min(seg.end, winEnd);
      const left = ((s0 - winStart) / winDur) * 100;
      const width = Math.max(0.4, ((s1 - s0) / winDur) * 100);
      const info = model.infoByIndex.get(seg.index);
      const hasOv = Boolean(info && info.partners.length);
      const title = spk + ' (' + clockPrecise(seg.start) + '–' + clockPrecise(seg.end) + '): ' + seg.text;
      blocks.push(
        '<div class="lane-block' + (hasOv ? ' has-ov' : '') + '"'
        + ' data-index="' + seg.index + '"'
        + ' style="left:' + left.toFixed(3) + '%;width:' + width.toFixed(3) + '%;'
        + '--spk-color:' + c.fg + ';--spk-bg:' + c.bg + ';--spk-bg-hi:' + c.bgHi + '"'
        + ' title="' + html(title) + '">' + html(seg.text) + '</div>'
      );
    }
    const isFiltered = filter === 'spk:' + spk;
    return '<div class="lane-row">'
      + '<div class="lane-label' + (isFiltered ? ' filtered' : '') + '"'
      + ' data-speaker="' + html(spk) + '" style="--spk-color:' + c.fg + '"'
      + ' title="Click to filter transcript to ' + html(spk) + '">' + html(spk) + '</div>'
      + '<div class="lane-track">' + ovBandsHtml + blocks.join('')
      + '<div class="lane-playhead"></div></div></div>';
  }).join('');
  body.innerHTML = rowsHtml;

  playheadEls = [...document.querySelectorAll('#lanes-stage .lane-playhead')];
  blockElsByIndex = new Map();
  body.querySelectorAll('.lane-block').forEach(el => {
    blockElsByIndex.set(Number(el.dataset.index), el);
  });
  updateLanesPlayhead();
}

function updateLanesPlayhead() {
  if (!model || lanesHidden || model.speakers.length < 2) return;
  const playerVideo = document.querySelector('#ytp video');
  const time = playerVideo ? (playerVideo.currentTime || 0) : 0;
  if (lanesFollow && (time < lanesWinStart + lanesWindow * 0.08 || time > lanesWinStart + lanesWindow * 0.85)) {
    const targetStart = clampWinStart(time - lanesWindow * 0.25);
    if (Math.abs(targetStart - lanesWinStart) > 0.25) {
      lanesWinStart = targetStart;
      renderLanesWindow(true);
      return;
    }
  }
  const pct = ((time - lanesWinStart) / lanesWindow) * 100;
  const visible = pct >= 0 && pct <= 100;
  for (const el of playheadEls) {
    el.style.display = visible ? '' : 'none';
    if (visible) el.style.left = pct.toFixed(3) + '%';
  }
  for (const [idx, blk] of blockElsByIndex.entries()) {
    const seg = model.byIndex.get(idx);
    blk.classList.toggle('now', Boolean(seg && time >= seg.start && time < seg.end));
  }
  const vp = document.getElementById('minimap-viewport');
  const mp = document.getElementById('minimap-playhead');
  if (vp) {
    vp.style.left = ((lanesWinStart / model.duration) * 100).toFixed(3) + '%';
    vp.style.width = Math.min(100, (lanesWindow / model.duration) * 100).toFixed(3) + '%';
  }
  if (mp) {
    mp.style.left = Math.min(100, (time / model.duration) * 100).toFixed(3) + '%';
  }
  if (!hoveredIndices) paintLanesStatus(time);
}

function paintLanesStatus(time) {
  const status = document.getElementById('lanes-status');
  if (!status || !model) return;
  const playerVideo = document.querySelector('#ytp video');
  const t = time != null ? time : (playerVideo ? (playerVideo.currentTime || 0) : 0);
  const ovIdx = model.overlapSpans.findIndex(ov => t >= ov.start && t <= ov.end);
  if (ovIdx !== -1) {
    const ov = model.overlapSpans[ovIdx];
    const who = [...ov.speakers].map(s => {
      const c = model.colorOf(s).fg;
      return '<strong style="color:' + c + '">' + html(s) + '</strong>';
    }).join(' + ');
    status.innerHTML = '<span class="ov-badge">∥ OVERLAP ' + (ovIdx + 1) + '/'
      + model.overlapSpans.length + '</span>' + who;
    return;
  }
  if (model.overlapSpans.length) {
    status.textContent = model.overlapSpans.length + ' simultaneous overlap'
      + (model.overlapSpans.length === 1 ? '' : 's') + ' · click to seek, drag to pan';
  } else {
    status.textContent = 'No overlapping turns · click to seek, drag to pan';
  }
}

function jumpOverlap(direction) {
  if (!model || !model.overlapSpans.length) return;
  const playerVideo = document.querySelector('#ytp video');
  const t = playerVideo ? (playerVideo.currentTime || 0) : 0;
  const spans = model.overlapSpans;
  let target = null;
  if (direction > 0) {
    target = spans.find(ov => ov.start > t + 0.5) || spans[0];
  } else {
    for (let i = spans.length - 1; i >= 0; i--) {
      if (spans[i].start < t - 1.0) { target = spans[i]; break; }
    }
    if (!target) target = spans[spans.length - 1];
  }
  lanesFollow = true;
  const followBtn = document.getElementById('lanes-follow');
  if (followBtn) followBtn.setAttribute('aria-pressed', 'true');
  const jumpTime = Math.max(0, target.start - 0.3);
  lanesWinStart = clampWinStart(target.start - lanesWindow * 0.25);
  renderLanesWindow(true);
  seekAndPlay(jumpTime);
  const firstIdx = Math.min(...target.indices);
  const row = rowByIndex.get(firstIdx);
  if (row && !row.hidden) row.scrollIntoView({block: 'center'});
}

// Swimlane controls & drag/click interactions
const toggleLanesBtn = document.getElementById('toggle-lanes');
if (toggleLanesBtn) {
  toggleLanesBtn.onclick = () => {
    lanesHidden = !lanesHidden;
    localStorage.setItem('ytarchive.transcript.lanesHidden', String(lanesHidden));
    setupLanes();
  };
}
const lanesWindowSel = document.getElementById('lanes-window');
if (lanesWindowSel) {
  lanesWindowSel.onchange = () => {
    lanesWindow = Number(lanesWindowSel.value) || 30;
    localStorage.setItem('ytarchive.transcript.lanesWindow', String(lanesWindow));
    const playerVideo = document.querySelector('#ytp video');
    const t = playerVideo ? (playerVideo.currentTime || 0) : lanesWinStart;
    lanesWinStart = clampWinStart(t - lanesWindow * 0.25);
    renderLanesWindow(true);
  };
}
const lanesFollowBtn = document.getElementById('lanes-follow');
if (lanesFollowBtn) {
  lanesFollowBtn.onclick = () => {
    lanesFollow = !lanesFollow;
    lanesFollowBtn.setAttribute('aria-pressed', String(lanesFollow));
    if (lanesFollow) updateLanesPlayhead();
  };
}
const prevOvBtn = document.getElementById('prev-overlap');
if (prevOvBtn) prevOvBtn.onclick = () => jumpOverlap(-1);
const nextOvBtn = document.getElementById('next-overlap');
if (nextOvBtn) nextOvBtn.onclick = () => jumpOverlap(1);

const lanesStage = document.getElementById('lanes-stage');
if (lanesStage) {
  let drag = null;
  lanesStage.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    const lbl = event.target.closest('.lane-label');
    if (lbl) {
      const sel = document.getElementById('transcript-filter');
      if (sel) {
        const val = 'spk:' + lbl.dataset.speaker;
        sel.value = sel.value === val ? 'all' : val;
        paintTranscript();
      }
      return;
    }
    const track = event.target.closest('.lane-track, .lanes-ruler');
    if (!track || !model) return;
    const rect = track.getBoundingClientRect();
    drag = {
      startX: event.clientX,
      startWin: lanesWinStart,
      rect,
      moved: false,
      pointerId: event.pointerId,
    };
    lanesStage.setPointerCapture(event.pointerId);
  });
  lanesStage.addEventListener('pointermove', event => {
    if (!drag) return;
    const dx = event.clientX - drag.startX;
    if (!drag.moved && Math.abs(dx) >= 5) {
      drag.moved = true;
      lanesFollow = false;
      if (lanesFollowBtn) lanesFollowBtn.setAttribute('aria-pressed', 'false');
    }
    if (drag.moved && drag.rect.width > 0) {
      lanesWinStart = clampWinStart(drag.startWin - (dx / drag.rect.width) * lanesWindow);
      renderLanesWindow(true);
    }
  });
  const finishDrag = event => {
    if (!drag) return;
    const wasDrag = drag.moved;
    const rect = drag.rect;
    drag = null;
    if (!wasDrag && rect.width > 0) {
      const frac = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
      seekAndPlay(lanesWinStart + frac * lanesWindow);
    }
  };
  lanesStage.addEventListener('pointerup', finishDrag);
  lanesStage.addEventListener('pointercancel', () => { drag = null; });

  lanesStage.addEventListener('wheel', event => {
    if (!model) return;
    if (Math.abs(event.deltaX) > Math.abs(event.deltaY) || event.shiftKey) {
      event.preventDefault();
      const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
      lanesFollow = false;
      if (lanesFollowBtn) lanesFollowBtn.setAttribute('aria-pressed', 'false');
      lanesWinStart = clampWinStart(lanesWinStart + (delta / 300) * lanesWindow * 0.4);
      renderLanesWindow(true);
    }
  }, {passive: false});

  lanesStage.addEventListener('mouseover', event => {
    const blk = event.target.closest('.lane-block');
    if (!blk || !model) {
      clearHoverLink();
      return;
    }
    const idx = Number(blk.dataset.index);
    const info = model.infoByIndex.get(idx);
    const set = new Set([idx]);
    if (info) info.partners.forEach(p => set.add(p.other.index));
    setHoverLink(set, idx, true);
  });
  lanesStage.addEventListener('mouseleave', clearHoverLink);
}

const minimapWrap = document.getElementById('lanes-minimap-wrap');
if (minimapWrap) {
  let scrubbing = false;
  const jumpMinimap = event => {
    if (!model) return;
    const rect = minimapWrap.getBoundingClientRect();
    if (rect.width <= 0) return;
    const frac = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    const t = frac * model.duration;
    lanesFollow = true;
    if (lanesFollowBtn) lanesFollowBtn.setAttribute('aria-pressed', 'true');
    lanesWinStart = clampWinStart(t - lanesWindow * 0.25);
    renderLanesWindow(true);
    seekAndPlay(t);
  };
  minimapWrap.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    scrubbing = true;
    minimapWrap.setPointerCapture(event.pointerId);
    jumpMinimap(event);
  });
  minimapWrap.addEventListener('pointermove', event => {
    if (scrubbing) jumpMinimap(event);
  });
  minimapWrap.addEventListener('pointerup', () => { scrubbing = false; });
  minimapWrap.addEventListener('pointercancel', () => { scrubbing = false; });
}

window.addEventListener('resize', () => {
  if (model && !lanesHidden && model.speakers.length >= 2) drawMinimap();
});

function syncTranscript(force) {
  const playerVideo = document.querySelector('#ytp video');
  if (!playerVideo || !transcript || !model) return;
  const time = playerVideo.currentTime || 0;
  const activeIndices = [];
  for (const seg of transcript.segments) {
    if (time >= seg.start && time < seg.end) activeIndices.push(seg.index);
  }
  const nextKey = activeIndices.join(',');
  if (force || nextKey !== activeKey) {
    activeKey = nextKey;
    const activeSet = new Set(activeIndices);
    for (const [idx, row] of rowByIndex.entries()) {
      row.classList.toggle('now', activeSet.has(idx));
    }
    if (activeIndices.length && !document.getElementById('transcript-search')?.value) {
      const firstRow = rowByIndex.get(activeIndices[0]);
      if (firstRow && !firstRow.hidden) firstRow.scrollIntoView({block: 'nearest'});
    }
  }

  // Word-level highlight inside all simultaneously active segments
  for (const span of activeWordSpans) span.classList.remove('word-now');
  activeWordSpans = [];
  for (const idx of activeIndices) {
    const row = rowByIndex.get(idx);
    if (!row || row.hidden) continue;
    const words = row.querySelectorAll('.word');
    for (const w of words) {
      const ws = Number(w.dataset.t);
      const we = Number(w.dataset.e);
      if (time >= ws && time <= Math.max(we, ws + 0.08)) {
        w.classList.add('word-now');
        activeWordSpans.push(w);
      }
    }
  }
  updateLanesPlayhead();
}

function tickLanes() {
  const playerVideo = document.querySelector('#ytp video');
  if (!playerVideo || playerVideo.paused || playerVideo.ended) {
    rafId = 0;
    return;
  }
  updateLanesPlayhead();
  rafId = requestAnimationFrame(tickLanes);
}

const transcriptSearch = document.getElementById('transcript-search');
if (transcriptSearch) transcriptSearch.addEventListener('input', paintTranscript);
const transcriptFilter = document.getElementById('transcript-filter');
if (transcriptFilter) transcriptFilter.addEventListener('change', paintTranscript);
const transcriptView = document.getElementById('transcript-view');
if (transcriptView) {
  transcriptView.value = localStorage.getItem('ytarchive.transcript.view') || 'full';
  transcriptView.onchange = () => {
    localStorage.setItem('ytarchive.transcript.view', transcriptView.value);
    document.getElementById('caption-box').classList.toggle('rolling', transcriptView.value === 'rolling');
    syncTranscript(true);
  };
  transcriptView.onchange();
}
const transcriptSize = document.getElementById('transcript-size');
if (transcriptSize) {
  transcriptSize.value = localStorage.getItem('ytarchive.transcript.size') || 'medium';
  transcriptSize.onchange = () => {
    localStorage.setItem('ytarchive.transcript.size', transcriptSize.value);
    const box = document.getElementById('caption-box');
    box.classList.remove('size-small', 'size-medium', 'size-large');
    box.classList.add('size-' + transcriptSize.value);
  };
  transcriptSize.onchange();
}
const playerVideo = document.querySelector('#ytp video');
if (playerVideo) {
  playerVideo.addEventListener('timeupdate', () => syncTranscript(false));
  playerVideo.addEventListener('seeked', () => syncTranscript(true));
  playerVideo.addEventListener('play', () => {
    if (!rafId) rafId = requestAnimationFrame(tickLanes);
  });
  playerVideo.addEventListener('pause', () => {
    if (rafId) { cancelAnimationFrame(rafId); rafId = 0; }
    syncTranscript(false);
  });
  playerVideo.addEventListener('ended', () => {
    if (rafId) { cancelAnimationFrame(rafId); rafId = 0; }
  });
}
const toggleCc = document.getElementById('toggle-cc');
if (toggleCc && playerVideo) {
  const track = playerVideo.textTracks[0];
  const paintCc = () => toggleCc.textContent =
    track && track.mode === 'showing' ? 'Disable video CC' : 'Enable video CC';
  toggleCc.onclick = () => {
    const player = window.MediaPlayer && MediaPlayer.get(playerVideo);
    if (player) player.toggleCaptions();
    else if (track) track.mode = track.mode === 'showing' ? 'hidden' : 'showing';
    paintCc();
  };
  document.getElementById('ytp').addEventListener('media-player:captions', paintCc);
  paintCc();
}
if (transcriptReady) loadTranscript().catch(err => {
  document.getElementById('transcript-meta').textContent = err.message;
});
