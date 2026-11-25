const {vid, site} = JSON.parse(document.getElementById('page-data').textContent);
const transcriptPanel = document.getElementById('transcript-panel');
const panelData = transcriptPanel ? transcriptPanel.dataset : {};
const hasOriginal = panelData.hasTranscript === 'true'
  || (panelData.hasTranscript === undefined && panelData.ready === 'true');
const hasEnglish = panelData.hasEnglish === 'true';
const hasEnglishDub = panelData.hasDub === 'true';
const englishEstDub = Number(panelData.estDub) || 0;
const englishEstNoDub = Number(panelData.estNoDub) || 0;
const openEnglishJobId = panelData.englishJobId || '';
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
      defaultBtn.textContent = hasOriginal
        ? 'Replace with multi-speaker'
        : 'Yes — transcribe multi-speaker';
    } else {
      defaultBtn.textContent = hasOriginal
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
      body: JSON.stringify({video_id: vid, config, force: hasOriginal}),
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

const englishDialog = document.getElementById('english-dialog');
const openEnglishBtn = document.getElementById('open-english');
const englishProgress = document.getElementById('english-progress');
const englishValue = document.getElementById('english-progress-value');
const englishLabel = document.getElementById('english-progress-label');
const englishFill = document.getElementById('english-progress-fill');
const englishDubCheck = document.getElementById('english-include-dub');
const englishEstimate = document.getElementById('english-estimate');

function setEnglishProgress(value, label) {
  if (!englishValue || !englishLabel || !englishFill) return;
  const pct = Math.max(0, Math.min(100, Math.round(Number(value || 0) * 100)));
  englishValue.textContent = pct + '%';
  englishLabel.textContent = label;
  englishFill.style.width = pct + '%';
}

function updateEnglishEstimate() {
  if (!englishEstimate || !englishDubCheck) return;
  if (englishDubCheck.checked) {
    englishEstimate.textContent =
      'Estimated time: ~' + englishEstDub + ' min with voiceover dub (~'
      + englishEstNoDub + ' min subtitles only).';
  } else {
    englishEstimate.textContent =
      'Estimated time: ~' + englishEstNoDub + ' min (subtitles and transcript only).';
  }
}

if (englishDubCheck) {
  englishDubCheck.addEventListener('change', updateEnglishEstimate);
}
if (openEnglishBtn && englishDialog) {
  openEnglishBtn.onclick = () => {
    updateEnglishEstimate();
    englishDialog.showModal();
  };
}
const cancelEnglishBtn = document.getElementById('cancel-english');
if (cancelEnglishBtn && englishDialog) {
  cancelEnglishBtn.onclick = () => englishDialog.close();
}

async function watchEnglishWork(jobId) {
  if (openEnglishBtn) openEnglishBtn.disabled = true;
  if (englishProgress) {
    englishProgress.hidden = false;
    englishProgress.className = 'transcript-progress';
  }
  try {
    for (;;) {
      const jres = await fetch('/api/work/' + encodeURIComponent(jobId), {cache: 'no-store'});
      const current = await jres.json();
      if (!jres.ok) throw new Error(current.error || jres.statusText);
      setEnglishProgress(current.progress || 0, current.message || current.status);
      if (current.status === 'done') {
        setEnglishProgress(1, 'English version ready — reloading');
        setTimeout(() => location.reload(), 500);
        return;
      }
      if (current.status === 'error') {
        throw new Error(current.error || 'English build failed');
      }
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  } catch (err) {
    if (englishProgress) englishProgress.classList.add('error');
    if (englishLabel) englishLabel.textContent = err.message;
    if (openEnglishBtn) openEnglishBtn.disabled = false;
  }
}

const startEnglishBtn = document.getElementById('start-english');
if (startEnglishBtn && englishDialog) {
  startEnglishBtn.onclick = async () => {
    englishDialog.close();
    if (openEnglishBtn) openEnglishBtn.disabled = true;
    if (englishProgress) {
      englishProgress.hidden = false;
      englishProgress.className = 'transcript-progress';
    }
    setEnglishProgress(0, 'queueing English build…');
    try {
      const config = {
        include_dub: Boolean(englishDubCheck && englishDubCheck.checked),
        source_language: (document.getElementById('english-source-lang')?.value || '').trim(),
      };
      const job = await post('/api/english', {
        video_id: vid,
        config,
        force: hasEnglish,
      });
      await watchEnglishWork(job.job_id);
    } catch (err) {
      if (englishProgress) englishProgress.classList.add('error');
      if (englishLabel) englishLabel.textContent = err.message;
      if (openEnglishBtn) openEnglishBtn.disabled = false;
    }
  };
}
if (openEnglishJobId) {
  setEnglishProgress(0, 'English build in progress…');
  watchEnglishWork(openEnglishJobId);
}

const mkvBtn = document.getElementById('export-english-mkv');
const vlcBtn = document.getElementById('open-english-vlc');
const mkvStatus = document.getElementById('english-mkv-status');

async function exportEnglishMkv(openVlc) {
  const buttons = [mkvBtn, vlcBtn].filter(Boolean);
  buttons.forEach(b => { b.disabled = true; });
  if (mkvStatus) {
    mkvStatus.className = 'meta';
    mkvStatus.textContent = openVlc ? 'Building MKV and opening in VLC…' : 'Building bilingual MKV…';
  }
  try {
    const data = await post('/api/english/mkv', {video_id: vid, open_vlc: Boolean(openVlc)});
    if (openVlc) {
      if (mkvStatus) mkvStatus.textContent = 'Opened bilingual MKV in VLC.';
    } else {
      const a = document.createElement('a');
      a.href = data.url;
      a.download = data.download || 'english.mkv';
      document.body.appendChild(a);
      a.click();
      a.remove();
      if (mkvStatus) mkvStatus.textContent = 'Bilingual MKV ready (' + (data.download || 'english.mkv') + ').';
    }
  } catch (err) {
    if (mkvStatus) {
      mkvStatus.className = 'meta error';
      mkvStatus.textContent = err.message;
    }
  } finally {
    buttons.forEach(b => { b.disabled = false; });
  }
}
if (mkvBtn) mkvBtn.onclick = () => exportEnglishMkv(false);
if (vlcBtn) vlcBtn.onclick = () => exportEnglishMkv(true);

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

let originalTranscript = null;
let englishTranscript = null;
const transcriptLangSel = document.getElementById('transcript-lang');
let transcriptLang = hasEnglish ? 'en' : 'orig';
let transcript = null;
let model = null;
let rowByIndex = new Map();
let activeKey = '';
let activeWordSpans = [];
let hoveredIndices = null;
let hoveredKey = '';

// Swimlane & dual-subtitle state
let lanesWindow = Number(localStorage.getItem('ytarchive.transcript.lanesWindow')) || 20;
let lanesHidden = localStorage.getItem('ytarchive.transcript.lanesHidden') === 'true';
let dualOverlay = localStorage.getItem('ytarchive.transcript.dualOverlay') === 'true';
let showDualSubs = localStorage.getItem('ytarchive.transcript.showDualSubs') !== 'false';
let splitView = localStorage.getItem('ytarchive.transcript.splitView') !== 'false';
let autoScroll = localStorage.getItem('ytarchive.transcript.autoScroll') !== 'false';
let lanesFollow = true;
let lanesWinStart = 0;
let renderedWinStart = -1;
let renderedWinDur = -1;
let playheadEls = [];
let blockElsByIndex = new Map();
let dualRowBySpeaker = new Map();
let dualHeadEl = null;
let ytpObserver = null;
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
  if (data.provider && data.language === 'en') {
    const src = data.source_language || 'auto';
    const dubTag = hasEnglishDub ? ' · voiceover dub' : '';
    return 'English (' + src + ' → en) · ' + data.provider + '/' + data.model + dubTag;
  }
  if (data.engine === 'multitalker-parakeet') return 'Nemotron-3 + Parakeet Ultra';
  const probability = Math.round((data.language_probability || 0) * 100);
  const lang = (data.language || 'unknown language') + (probability ? ' ' + probability + '%' : '');
  return lang + ' · ' + (data.model || 'Whisper');
}

function isEnglishMode() {
  return (transcriptLang === 'en' || transcriptLang === 'both') && Boolean(englishTranscript);
}

function applyActiveTranscript() {
  const active = transcriptLang === 'orig'
    ? (originalTranscript || englishTranscript)
    : (englishTranscript || originalTranscript);
  if (active) setTranscriptData(active);
}

async function loadTranscript() {
  const tasks = [];
  if (hasEnglish) {
    tasks.push(
      fetch('/api/english/' + encodeURIComponent(vid)).then(async res => {
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || res.statusText);
        englishTranscript = data;
      })
    );
  }
  if (hasOriginal) {
    tasks.push(
      fetch('/api/transcript/' + encodeURIComponent(vid)).then(async res => {
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || res.statusText);
        originalTranscript = data;
      })
    );
  }
  await Promise.all(tasks);
  if (transcriptLangSel) {
    const saved = localStorage.getItem('ytarchive.transcript.lang');
    if (saved && [...transcriptLangSel.options].some(o => o.value === saved)) {
      transcriptLangSel.value = saved;
      transcriptLang = saved;
    } else {
      transcriptLang = transcriptLangSel.value || 'en';
    }
    transcriptLangSel.onchange = () => {
      transcriptLang = transcriptLangSel.value;
      localStorage.setItem('ytarchive.transcript.lang', transcriptLang);
      applyActiveTranscript();
    };
  }
  applyActiveTranscript();
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
  const showBoth = transcriptLang === 'both' && isEnglishMode();
  const cls = 'segment'
    + (matched ? ' match' : '')
    + (info && info.overlapsPrev ? ' ov-child' : '')
    + (showBoth ? ' segment-both' : '');
  const origCol = showBoth
    ? '<div class="segment-orig">' + html(segment.text_orig || '') + '</div>'
    : '';
  return '<div class="' + cls + '"'
    + (hidden ? ' hidden' : '')
    + ' style="--spk-color:' + color.fg + '"'
    + ' data-index="' + segment.index + '" data-start="' + Number(segment.start)
    + '" data-end="' + Number(segment.end) + '"'
    + (partnerIds ? ' data-partners="' + partnerIds + '"' : '')
    + '><span class="segment-time">' + clock(segment.start) + ovMark + '</span>'
    + '<button type="button" class="segment-speaker" title="assign or group speaker: '
    + html(segment.speaker || 'Unassigned') + '">' + html(segment.speaker || 'Unassigned') + '</button>'
    + '<div class="segment-text">' + body + '</div>' + origCol + '</div>';
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
      || (segment.text_orig || '').toLowerCase().includes(query)
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

function scrollRowIntoCaptionBox(row, center) {
  const box = document.getElementById('caption-box');
  if (!box || !row || row.hidden) return;
  const boxRect = box.getBoundingClientRect();
  const rowRect = row.getBoundingClientRect();
  if (center) {
    const delta = (rowRect.top + rowRect.height / 2) - (boxRect.top + boxRect.height / 2);
    box.scrollTop += delta;
    return;
  }
  const topDelta = rowRect.top - boxRect.top;
  const bottomDelta = rowRect.bottom - boxRect.bottom;
  if (topDelta < 0) {
    box.scrollTop += topDelta - 12;
  } else if (bottomDelta > 0) {
    box.scrollTop += bottomDelta + 12;
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
        if (row && !row.hidden) scrollRowIntoCaptionBox(row, true);
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
  const english = isEnglishMode();
  const res = await fetch(english ? '/api/english/speaker' : '/api/transcript/speaker', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({video_id: vid, segment_index: index, speaker}),
  });
  const data = await res.json();
  if (!res.ok) return alert(data.error || res.statusText);
  if (english) englishTranscript = data;
  else originalTranscript = data;
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
  const english = isEnglishMode();
  const res = await fetch(english ? '/api/english/rename-speaker' : '/api/transcript/rename-speaker', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({video_id: vid, old: oldName, new: newName}),
  });
  const data = await res.json();
  if (!res.ok) return alert(data.error || res.statusText);
  document.getElementById('speaker-new').value = '';
  if (english) englishTranscript = data;
  else originalTranscript = data;
  setTranscriptData(data);
};

// ---------------- Dual-Color Live Subtitles, Swimlanes & Minimap ----------------
function ensureOverlapDom() {
  if (!document.getElementById('dual-stage')) {
    const stage = document.createElement('section');
    stage.className = 'dual-stage';
    stage.id = 'dual-stage';
    stage.hidden = true;
    stage.innerHTML =
      '<div class="dual-subs" id="dual-subs" aria-live="polite"></div>'
      + '<div class="speaker-lanes" id="speaker-lanes">'
      + '  <div class="lanes-head">'
      + '    <span class="lanes-title">Speaker overlap timeline</span>'
      + '    <span class="lanes-status" id="lanes-status"></span>'
      + '    <div class="lanes-tools">'
      + '      <button type="button" id="prev-overlap" title="Jump to previous overlapping turn">◀ Prev overlap</button>'
      + '      <button type="button" id="next-overlap" title="Jump to next overlapping turn">Next overlap ▶</button>'
      + '      <label>Window <select id="lanes-window">'
      + '        <option value="10">10s</option>'
      + '        <option value="20" selected>20s</option>'
      + '        <option value="30">30s</option>'
      + '        <option value="60">60s</option>'
      + '      </select></label>'
      + '      <button type="button" id="lanes-follow" aria-pressed="true" title="Keep timeline synced to playback">Follow</button>'
      + '      <button type="button" id="toggle-dual-subs" aria-pressed="true" title="Show or hide live dual-color subtitle rows">Live subs</button>'
      + '      <button type="button" id="toggle-overlay-subs" aria-pressed="false" title="Show dual-color speaker subtitles over the video frame">Overlay on video</button>'
      + '      <button type="button" id="toggle-split-view" aria-pressed="true" title="Side-by-side video and transcript with full-width timeline below">Split view</button>'
      + '    </div>'
      + '  </div>'
      + '  <div class="lanes-stage" id="lanes-stage">'
      + '    <div class="lanes-ruler-row">'
      + '      <div class="lanes-gutter"></div>'
      + '      <div class="lanes-ruler" id="lanes-ruler"></div>'
      + '    </div>'
      + '    <div class="lanes-body" id="lanes-body"></div>'
      + '  </div>'
      + '  <div class="lanes-minimap-wrap" id="lanes-minimap-wrap" title="Click or drag to jump anywhere in the video">'
      + '    <canvas class="lanes-minimap" id="lanes-minimap" height="24"></canvas>'
      + '    <div class="minimap-viewport" id="minimap-viewport"></div>'
      + '    <div class="minimap-playhead" id="minimap-playhead"></div>'
      + '  </div>'
      + '</div>';
    const ytp = document.getElementById('ytp');
    if (ytp && ytp.parentNode) {
      ytp.insertAdjacentElement('afterend', stage);
    } else if (captionBox && captionBox.parentNode) {
      captionBox.insertAdjacentElement('beforebegin', stage);
    }
  }
  const controls = document.querySelector('.transcript-controls');
  if (controls && !document.getElementById('transcript-filter')) {
    const wrap = document.createElement('label');
    wrap.id = 'transcript-filter-wrap';
    wrap.hidden = true;
    wrap.innerHTML = 'Filter <select id="transcript-filter"><option value="all">all segments</option></select>';
    const search = document.getElementById('transcript-search');
    if (search) search.insertAdjacentElement('afterend', wrap);
    else controls.prepend(wrap);
  }
  if (controls && !document.getElementById('toggle-autoscroll')) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.id = 'toggle-autoscroll';
    btn.setAttribute('aria-pressed', String(autoScroll));
    btn.title = 'Scroll the transcript list as playback moves';
    btn.textContent = 'Auto-scroll';
    const cc = document.getElementById('toggle-cc');
    if (cc) cc.insertAdjacentElement('beforebegin', btn);
    else controls.appendChild(btn);
  }
  if (controls && !document.getElementById('toggle-lanes')) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.id = 'toggle-lanes';
    btn.hidden = true;
    btn.textContent = 'Hide overlap view';
    const cc = document.getElementById('toggle-cc');
    if (cc) cc.insertAdjacentElement('beforebegin', btn);
    else controls.appendChild(btn);
  }
}
ensureOverlapDom();

// Fallback for pre-restart server HTML: wrap huge framesheets and shot grids in collapsible <details>.
function ensureCollapsibleSheets() {
  const main = document.querySelector('main');
  if (!main) return;
  if (!document.getElementById('framesheets-section')) {
    const sheetImgs = [...main.querySelectorAll(':scope > a > img.sheet')];
    if (sheetImgs.length) {
      const nodes = [];
      for (const img of sheetImgs) {
        const a = img.parentElement;
        const prev = a && a.previousElementSibling;
        if (prev && prev.classList.contains('sheet-label')) nodes.push(prev);
        if (a) nodes.push(a);
      }
      if (nodes.length) {
        const details = document.createElement('details');
        details.className = 'sheets-toggle';
        details.id = 'framesheets-section';
        if (sheetImgs.length <= 1) details.open = true;
        const summary = document.createElement('summary');
        summary.textContent = sheetImgs.length > 1
          ? 'Framesheets (' + sheetImgs.length + ' sheets)'
          : 'Framesheet';
        details.appendChild(summary);
        nodes[0].parentNode.insertBefore(details, nodes[0]);
        for (const n of nodes) details.appendChild(n);
      }
    }
  }
  if (!document.getElementById('shots-section')) {
    const shotsEl = main.querySelector(':scope > .shots');
    if (shotsEl) {
      const count = shotsEl.querySelectorAll('figure').length;
      const prev = shotsEl.previousElementSibling;
      const details = document.createElement('details');
      details.className = 'sheets-toggle';
      details.id = 'shots-section';
      if (count <= 120) details.open = true;
      const summary = document.createElement('summary');
      summary.textContent = 'Shot images (' + count + ' shots) — click to play from that moment';
      details.appendChild(summary);
      shotsEl.parentNode.insertBefore(details, prev && prev.classList.contains('sheet-label') ? prev : shotsEl);
      if (prev && prev.classList.contains('sheet-label')) prev.remove();
      details.appendChild(shotsEl);
    }
  }
}
ensureCollapsibleSheets();

function clampWinStart(start) {
  if (!model) return 0;
  return Math.max(0, Math.min(start, Math.max(0, model.duration - lanesWindow)));
}

let ovWinStart = 0;
let ovRenderedStart = -1;
let ovRenderedTrackPx = 0;
let ovPlayheadEls = [];
let ovBlockElsByIndex = new Map();
let ovResizeObserver = null;
const OV_WINDOW_SEC = 10;

function applyDualOverlayPosition() {
  const dualSubs = document.getElementById('dual-subs');
  const dualStage = document.getElementById('dual-stage');
  const ytp = document.getElementById('ytp');
  const overlayBtn = document.getElementById('toggle-overlay-subs');
  const subsBtn = document.getElementById('toggle-dual-subs');
  if (!dualSubs || !dualStage) return;
  const visible = Boolean(showDualSubs && model && model.speakers.length >= 2 && !lanesHidden);
  dualSubs.hidden = !visible;
  const activeOverlay = Boolean(visible && dualOverlay && ytp);
  dualSubs.classList.toggle('is-overlay', activeOverlay);
  if (activeOverlay) {
    const host = ytp.querySelector('.mp') || ytp;
    if (dualSubs.parentNode !== host) host.appendChild(dualSubs);
    ovRenderedStart = -1;
    requestAnimationFrame(() => {
      const pv = document.querySelector('#ytp video');
      updateOverlayTimeline(pv ? (pv.currentTime || 0) : 0, true);
    });
  } else {
    if (dualSubs.parentNode !== dualStage) dualStage.prepend(dualSubs);
  }
  if (subsBtn) subsBtn.setAttribute('aria-pressed', String(showDualSubs));
  if (overlayBtn) {
    overlayBtn.setAttribute('aria-pressed', String(dualOverlay));
    overlayBtn.textContent = dualOverlay ? 'Dock below video' : 'Overlay on video';
  }
}

function syncSplitHeight() {
  const main = document.querySelector('main');
  const ytp = document.getElementById('ytp');
  const panel = document.getElementById('transcript-panel');
  if (!main || !ytp || !panel) return;
  const active = main.classList.contains('split-view') && window.innerWidth >= 1000;
  if (!active) {
    panel.style.height = '';
    return;
  }
  const h = Math.round(ytp.getBoundingClientRect().height);
  if (h >= 200) panel.style.height = h + 'px';
}

function applySplitLayout() {
  const main = document.querySelector('main');
  const ytp = document.getElementById('ytp');
  const splitBtn = document.getElementById('toggle-split-view');
  if (!main) return;
  const active = Boolean(
    splitView && ytp && model && model.speakers.length >= 2 && !lanesHidden
  );
  main.classList.toggle('split-view', active);
  if (splitBtn) {
    splitBtn.setAttribute('aria-pressed', String(splitView));
    splitBtn.textContent = splitView ? 'Split view' : 'Stacked view';
  }
  if (active && ytp && !ytpObserver && typeof ResizeObserver !== 'undefined') {
    ytpObserver = new ResizeObserver(() => {
      syncSplitHeight();
      if (model && !lanesHidden && model.speakers.length >= 2) drawMinimap();
    });
    ytpObserver.observe(ytp);
  }
  requestAnimationFrame(() => {
    syncSplitHeight();
    if (model && !lanesHidden && model.speakers.length >= 2) drawMinimap();
  });
}

function renderDualSubsShell() {
  const dualSubs = document.getElementById('dual-subs');
  if (!dualSubs || !model || model.speakers.length < 2) return;
  const rows = model.speakers.map(spk => {
    const c = model.colorOf(spk);
    return '<div class="dual-sub-row" data-speaker="' + html(spk) + '" style="--spk-color:' + c.fg + '">'
      + '<div class="dual-sub-spk"><span class="dual-sub-dot"></span><span>' + html(spk) + '</span></div>'
      + '<div class="dual-sub-text"></div>'
      + '<div class="dual-sub-time"></div>'
      + '<div class="dual-sub-prog"></div>'
      + '</div>';
  }).join('');
  dualSubs.innerHTML = '<div class="dual-sub-head" id="dual-sub-head"></div>' + rows
    + '<div class="ov-timeline" id="ov-timeline">'
    + '<div class="ov-ruler-row"><div class="ov-gutter"></div><div class="ov-ruler" id="ov-ruler"></div></div>'
    + '<div class="ov-body" id="ov-body"></div>'
    + '</div>';
  dualHeadEl = document.getElementById('dual-sub-head');
  dualRowBySpeaker = new Map();
  dualSubs.querySelectorAll('.dual-sub-row').forEach(row => {
    dualRowBySpeaker.set(row.dataset.speaker, {
      row,
      textEl: row.querySelector('.dual-sub-text'),
      timeEl: row.querySelector('.dual-sub-time'),
      progEl: row.querySelector('.dual-sub-prog'),
      renderedSegIdx: -1,
    });
  });
  ovRenderedStart = -1;
  ovRenderedTrackPx = 0;
  const ovTimeline = document.getElementById('ov-timeline');
  if (ovTimeline) {
    ovTimeline.addEventListener('pointerdown', event => {
      event.stopPropagation();
    });
    ovTimeline.addEventListener('click', event => {
      event.stopPropagation();
      const track = event.target.closest('.ov-track, .ov-ruler');
      if (!track || !model) return;
      const rect = track.getBoundingClientRect();
      if (rect.width <= 0) return;
      const frac = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
      const winDur = Math.min(OV_WINDOW_SEC, Math.max(4, lanesWindow));
      seekAndPlay(ovWinStart + frac * winDur);
    });
  }
  const ovRuler = document.getElementById('ov-ruler');
  if (ovRuler && typeof ResizeObserver !== 'undefined') {
    if (ovResizeObserver) ovResizeObserver.disconnect();
    ovResizeObserver = new ResizeObserver(() => {
      if (dualOverlay && showDualSubs && model && !lanesHidden && model.speakers.length >= 2) {
        const pv = document.querySelector('#ytp video');
        updateOverlayTimeline(pv ? (pv.currentTime || 0) : 0, true);
      }
    });
    ovResizeObserver.observe(ovRuler);
  }
}

function updateOverlayTimeline(time, force) {
  const dualSubs = document.getElementById('dual-subs');
  if (!dualSubs || !dualSubs.classList.contains('is-overlay') || !model || model.speakers.length < 2) return;
  const ruler = document.getElementById('ov-ruler');
  const body = document.getElementById('ov-body');
  if (!ruler || !body) return;

  const winDur = Math.min(OV_WINDOW_SEC, Math.max(4, lanesWindow));
  const maxStart = Math.max(0, model.duration - winDur);
  const trackPx = ruler.getBoundingClientRect().width || 0;
  const outOfView = ovRenderedStart < 0
    || time < ovWinStart + winDur * 0.05
    || time > ovWinStart + winDur * 0.84;

  if (force || outOfView || Math.abs(trackPx - ovRenderedTrackPx) >= 2) {
    if (outOfView || ovRenderedStart < 0) {
      ovWinStart = Math.max(0, Math.min(maxStart, time - winDur * 0.14));
    } else {
      ovWinStart = Math.max(0, Math.min(maxStart, ovWinStart));
    }
    ovRenderedStart = ovWinStart;
    ovRenderedTrackPx = trackPx;
    const winStart = ovWinStart;
    const winEnd = winStart + winDur;
    const measurer = trackPx > 0 ? getLaneMeasurer() : null;

    const step = winDur <= 10 ? 2 : 4;
    const firstTick = Math.ceil(winStart / step) * step;
    const ticks = [];
    for (let t = firstTick; t <= winEnd; t += step) {
      const pct = ((t - winStart) / winDur) * 100;
      ticks.push('<span class="ruler-tick" style="left:' + pct.toFixed(3) + '%">' + clock(t) + '</span>');
    }
    const visibleOverlaps = model.overlapSpans.filter(ov => ov.end > winStart && ov.start < winEnd);
    const pills = visibleOverlaps.map(ov => {
      const left = ((Math.max(ov.start, winStart) - winStart) / winDur) * 100;
      const right = ((Math.min(ov.end, winEnd) - winStart) / winDur) * 100;
      const width = Math.max(0.25, right - left);
      if (width < 3.5) return '';
      const dur = Math.max(0.1, ov.end - ov.start);
      return '<span class="ruler-ov-pill" style="left:' + left.toFixed(3)
        + '%;max-width:' + width.toFixed(3) + '%">∥ ' + dur.toFixed(1) + 's</span>';
    }).join('');
    ruler.innerHTML = ticks.join('') + pills + '<div class="lane-playhead"></div>';

    const bands = visibleOverlaps.map(ov => {
      const left = ((Math.max(ov.start, winStart) - winStart) / winDur) * 100;
      const right = ((Math.min(ov.end, winEnd) - winStart) / winDur) * 100;
      return '<div class="lane-ov-band" style="left:' + left.toFixed(3)
        + '%;width:' + Math.max(0.25, right - left).toFixed(3) + '%"></div>';
    }).join('');

    body.innerHTML = model.speakers.map(spk => {
      const c = model.colorOf(spk);
      const segs = model.bySpeaker.get(spk) || [];
      const blocks = [];
      for (const seg of segs) {
        if (seg.end <= winStart) continue;
        if (seg.start >= winEnd) break;
        const s0 = Math.max(seg.start, winStart);
        const s1 = Math.min(seg.end, winEnd);
        const left = ((s0 - winStart) / winDur) * 100;
        const width = Math.max(0.5, ((s1 - s0) / winDur) * 100);
        const blockPx = trackPx > 0 ? (trackPx * width) / 100 : 0;
        const info = model.infoByIndex.get(seg.index);
        const hasOv = Boolean(info && info.partners.length);
        const content = buildLaneBlockContent(seg, s0, s1, blockPx, info, measurer);
        blocks.push(
          '<div class="lane-block' + (hasOv ? ' has-ov' : '') + content.cls + '"'
          + ' data-index="' + seg.index + '"'
          + ' style="left:' + left.toFixed(3) + '%;width:' + width.toFixed(3) + '%;'
          + '--spk-color:' + c.fg + ';--spk-bg:' + c.bg + ';--spk-bg-hi:' + c.bgHi + '">'
          + content.inner + '</div>'
        );
      }
      return '<div class="ov-row">'
        + '<div class="ov-label" style="--spk-color:' + c.fg + '">' + html(spk) + '</div>'
        + '<div class="ov-track">' + bands + blocks.join('')
        + '<div class="lane-playhead"></div></div></div>';
    }).join('');

    ovPlayheadEls = [...dualSubs.querySelectorAll('.ov-timeline .lane-playhead')];
    ovBlockElsByIndex = new Map();
    body.querySelectorAll('.lane-block').forEach(el => {
      ovBlockElsByIndex.set(Number(el.dataset.index), el);
    });
  }

  const pct = ((time - ovWinStart) / winDur) * 100;
  const visible = pct >= 0 && pct <= 100;
  for (const el of ovPlayheadEls) {
    el.style.display = visible ? '' : 'none';
    if (visible) el.style.left = pct.toFixed(3) + '%';
  }
  for (const [idx, blk] of ovBlockElsByIndex.entries()) {
    const seg = model.byIndex.get(idx);
    const isNow = Boolean(seg && time >= seg.start && time < seg.end);
    blk.classList.toggle('now', isNow);
    if (isNow) {
      blk.querySelectorAll('.lane-word').forEach(w => {
        const ws = Number(w.dataset.t);
        const we = Number(w.dataset.e);
        w.classList.toggle('lw-now', time >= ws && time <= Math.max(we, ws + 0.08));
      });
    } else {
      blk.querySelectorAll('.lane-word.lw-now').forEach(w => w.classList.remove('lw-now'));
    }
  }
}

function dualWordsHtml(seg) {
  const info = model.infoByIndex.get(seg.index);
  const partners = info ? info.partners : [];
  const words = seg.words || [];
  if (!words.length) return html(seg.text);
  return words.map((word, wi) => {
    const gap = wi && !/^[,.;:!?)]/.test(word.word) ? ' ' : '';
    const wStart = word.start != null ? Number(word.start) : Number(seg.start);
    const wEnd = word.end != null ? Number(word.end) : wStart;
    let ovColor = '';
    for (const p of partners) {
      if (wStart < p.ovEnd && wEnd > p.ovStart) {
        ovColor = model.colorOf(p.other.speaker).fg;
        break;
      }
    }
    return gap + '<span class="word' + (ovColor ? ' word-ov' : '') + '"'
      + ' data-t="' + wStart + '" data-e="' + wEnd + '"'
      + (ovColor ? ' style="--ov-color:' + ovColor + '"' : '')
      + '>' + html(word.word) + '</span>';
  }).join('');
}

function updateDualSubs(time) {
  if (!model || lanesHidden || model.speakers.length < 2 || !dualRowBySpeaker.size) return;
  if (dualOverlay && showDualSubs) {
    updateOverlayTimeline(time, false);
    return;
  }
  const activeBySpeaker = new Map();
  for (const spk of model.speakers) {
    const segs = model.bySpeaker.get(spk) || [];
    let found = null;
    for (const seg of segs) {
      if (seg.start > time) break;
      if (time >= seg.start && time < seg.end) {
        found = seg;
        break;
      }
    }
    if (found) activeBySpeaker.set(spk, found);
  }
  const activeList = [...activeBySpeaker.values()].sort((a, b) => a.start - b.start);
  const isOverlapping = activeList.length >= 2;

  if (dualHeadEl) {
    if (isOverlapping) {
      const first = activeList[0];
      const second = activeList[1];
      const ovStart = Math.max(first.start, second.start);
      const ovEnd = Math.min(first.end, second.end);
      const ovDur = Math.max(0, ovEnd - ovStart);
      const offset = Math.max(0, second.start - first.start);
      const detail = offset >= 0.1
        ? html(second.speaker) + ' cut in +' + offset.toFixed(1) + 's into ' + html(first.speaker)
        : 'both started together';
      dualHeadEl.innerHTML = '<span class="ov-live">∥ OVERLAPPING · ' + ovDur.toFixed(1)
        + 's</span><span>' + detail + '</span>';
    } else {
      const legend = model.speakers.map(s => {
        const c = model.colorOf(s).fg;
        return '<span style="color:' + c + '">' + html(s) + '</span>';
      }).join(' · ');
      dualHeadEl.innerHTML = '<span>Dual speaker captions: ' + legend + '</span>';
    }
  }

  for (const [spk, slot] of dualRowBySpeaker.entries()) {
    const seg = activeBySpeaker.get(spk) || null;
    if (!seg) {
      slot.row.classList.remove('active', 'overlapping');
      if (slot.renderedSegIdx !== -1) {
        slot.textEl.textContent = '';
        slot.timeEl.textContent = '';
        slot.progEl.style.width = '0%';
        slot.renderedSegIdx = -1;
      }
      continue;
    }
    slot.row.classList.add('active');
    slot.row.classList.toggle('overlapping', isOverlapping);
    if (slot.renderedSegIdx !== seg.index) {
      slot.textEl.innerHTML = dualWordsHtml(seg);
      const dur = Math.max(0.1, seg.end - seg.start);
      slot.timeEl.textContent = clockPrecise(seg.start) + '–' + clockPrecise(seg.end)
        + ' (' + dur.toFixed(1) + 's)';
      slot.renderedSegIdx = seg.index;
    }
    const frac = Math.max(0, Math.min(1, (time - seg.start) / Math.max(0.05, seg.end - seg.start)));
    slot.progEl.style.width = (frac * 100).toFixed(1) + '%';
    slot.textEl.querySelectorAll('.word').forEach(w => {
      const ws = Number(w.dataset.t);
      const we = Number(w.dataset.e);
      w.classList.toggle('word-now', time >= ws && time <= Math.max(we, ws + 0.08));
    });
  }
}

function setupLanes() {
  const dualStage = document.getElementById('dual-stage');
  const toggleBtn = document.getElementById('toggle-lanes');
  const winSel = document.getElementById('lanes-window');
  const followBtn = document.getElementById('lanes-follow');
  if (!dualStage || !model) return;
  const multi = model.speakers.length >= 2;
  if (toggleBtn) toggleBtn.hidden = !multi;
  if (!multi) {
    dualStage.hidden = true;
    applyDualOverlayPosition();
    applySplitLayout();
    return;
  }
  dualStage.hidden = lanesHidden;
  if (toggleBtn) toggleBtn.textContent = lanesHidden ? 'Show overlap view' : 'Hide overlap view';
  if (winSel) {
    if (![...winSel.options].some(o => Number(o.value) === lanesWindow)) lanesWindow = 20;
    winSel.value = String(lanesWindow);
  }
  if (followBtn) followBtn.setAttribute('aria-pressed', String(lanesFollow));
  const prevBtn = document.getElementById('prev-overlap');
  const nextBtn = document.getElementById('next-overlap');
  const hasOverlaps = model.overlapSpans.length > 0;
  if (prevBtn) prevBtn.disabled = !hasOverlaps;
  if (nextBtn) nextBtn.disabled = !hasOverlaps;
  renderedWinStart = -1;
  renderedWinDur = -1;
  renderDualSubsShell();
  applyDualOverlayPosition();
  applySplitLayout();
  if (!lanesHidden) {
    drawMinimap();
    renderLanesWindow(true);
    paintLanesStatus();
    const pv = document.querySelector('#ytp video');
    updateDualSubs(pv ? (pv.currentTime || 0) : 0);
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
  const height = Math.round(24 * (window.devicePixelRatio || 1));
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

let laneMeasureCtx = null;
let laneMeasureFont = '';
let lastLaneTrackPx = 0;

function getLaneMeasurer() {
  if (!laneMeasureCtx) {
    const canvas = document.createElement('canvas');
    laneMeasureCtx = canvas.getContext('2d');
  }
  const rootPx = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
  const family = getComputedStyle(document.body).fontFamily || 'sans-serif';
  const font = '600 ' + (0.82 * rootPx).toFixed(2) + 'px ' + family;
  if (laneMeasureCtx && font !== laneMeasureFont) {
    laneMeasureCtx.font = font;
    laneMeasureFont = font;
  }
  return laneMeasureCtx;
}

function laneTokensForSpan(seg, s0, s1) {
  const rawWords = seg.words || [];
  let tokens = [];
  if (rawWords.length) {
    for (const w of rawWords) {
      const txt = String(w.word || '').trim();
      if (!txt) continue;
      const ws = w.start != null ? Number(w.start) : Number(seg.start);
      const we = w.end != null ? Number(w.end) : ws;
      if (tokens.length && /^[,.;:!?)]/.test(txt)) {
        const prev = tokens[tokens.length - 1];
        prev.word += txt;
        if (we > prev.end) prev.end = we;
      } else {
        tokens.push({word: txt, start: ws, end: Math.max(ws, we)});
      }
    }
  } else {
    const parts = String(seg.text || '').trim().split(/\s+/).filter(Boolean);
    const totalWeight = parts.reduce((acc, w) => acc + w.length + 1, 0) || 1;
    const dur = Math.max(0.001, Number(seg.end) - Number(seg.start));
    let acc = 0;
    for (const part of parts) {
      const wt = part.length + 1;
      const ws = Number(seg.start) + (acc / totalWeight) * dur;
      acc += wt;
      const we = Number(seg.start) + (acc / totalWeight) * dur;
      tokens.push({word: part, start: ws, end: we});
    }
  }
  const visible = tokens.filter(t => t.end > s0 + 0.005 && t.start < s1 - 0.005);
  return visible.length ? visible : tokens.slice(0, 1);
}

// 1D Pool Adjacent Violators: globally optimal least-squares alignment of words to their
// spoken onset times inside [0, innerPx] without any pair of words overlapping.
function solveTimedWordPositions(ideals, widths, spacePx, innerPx, totalPx) {
  const n = ideals.length;
  const maxShift = Math.max(0, innerPx - totalPx);
  const cum = new Array(n);
  const blocks = [];
  let cur = 0;
  for (let i = 0; i < n; i++) {
    cum[i] = cur;
    blocks.push({sum: ideals[i] - cur, count: 1});
    while (
      blocks.length >= 2
      && (blocks[blocks.length - 2].sum / blocks[blocks.length - 2].count)
         > (blocks[blocks.length - 1].sum / blocks[blocks.length - 1].count)
    ) {
      const last = blocks.pop();
      blocks[blocks.length - 1].sum += last.sum;
      blocks[blocks.length - 1].count += last.count;
    }
    cur += widths[i] + spacePx;
  }
  const xs = new Array(n);
  let idx = 0;
  for (const blk of blocks) {
    const shift = Math.max(0, Math.min(maxShift, blk.sum / blk.count));
    for (let k = 0; k < blk.count; k++) {
      xs[idx] = shift + cum[idx];
      idx += 1;
    }
  }
  return xs;
}

function buildLaneBlockContent(seg, s0, s1, blockPx, info, ctx) {
  const pad = 5;
  const innerPx = blockPx - pad * 2;
  if (!ctx || innerPx < 28) {
    return {cls: '', inner: html(seg.text)};
  }
  const tokens = laneTokensForSpan(seg, s0, s1);
  if (!tokens.length) return {cls: '', inner: ''};

  const spacePx = Math.max(4, Math.ceil(ctx.measureText(' ').width));
  const widths = tokens.map(t => Math.ceil(ctx.measureText(t.word).width));
  const totalPx = widths.reduce((a, b) => a + b, 0) + Math.max(0, tokens.length - 1) * spacePx;
  const sDur = Math.max(0.001, s1 - s0);
  const partners = info ? info.partners : [];

  let xs;
  let clipped = false;
  if (totalPx <= innerPx) {
    const ideals = tokens.map((t, i) => {
      const raw = ((t.start - s0) / sDur) * innerPx;
      return Math.max(0, Math.min(innerPx - widths[i], raw));
    });
    xs = solveTimedWordPositions(ideals, widths, spacePx, innerPx, totalPx);
  } else {
    clipped = true;
    xs = [];
    let cur = 0;
    for (let i = 0; i < tokens.length; i++) {
      xs.push(cur);
      cur += widths[i] + spacePx;
    }
  }

  const spans = [];
  for (let i = 0; i < tokens.length; i++) {
    if (clipped && xs[i] >= innerPx) break;
    const tok = tokens[i];
    let ovColor = '';
    if (partners.length) {
      for (const p of partners) {
        if (tok.start < p.ovEnd && tok.end > p.ovStart) {
          ovColor = model.colorOf(p.other.speaker).fg;
          break;
        }
      }
    }
    const leftPct = ((pad + xs[i]) / blockPx) * 100;
    spans.push(
      '<span class="lane-word' + (ovColor ? ' lw-ov' : '') + '"'
      + ' data-t="' + tok.start + '" data-e="' + tok.end + '"'
      + ' style="left:' + leftPct.toFixed(3) + '%'
      + (ovColor ? ';--ov-color:' + ovColor : '') + '">'
      + html(tok.word) + '</span>'
    );
  }
  return {
    cls: ' has-words' + (clipped ? ' is-clipped' : ''),
    inner: '<div class="lane-words">' + spans.join('') + '</div>',
  };
}

function renderLanesWindow(force) {
  if (!model || lanesHidden || model.speakers.length < 2) return;
  const winStart = clampWinStart(lanesWinStart);
  const winDur = lanesWindow;
  const ruler = document.getElementById('lanes-ruler');
  const body = document.getElementById('lanes-body');
  if (!ruler || !body) return;
  const trackPx = ruler.getBoundingClientRect().width || 0;
  if (
    !force
    && Math.abs(winStart - renderedWinStart) < 0.001
    && winDur === renderedWinDur
    && Math.abs(trackPx - lastLaneTrackPx) < 1
  ) {
    return;
  }
  lanesWinStart = winStart;
  renderedWinStart = winStart;
  renderedWinDur = winDur;
  lastLaneTrackPx = trackPx;
  const winEnd = winStart + winDur;
  const measurer = trackPx > 0 ? getLaneMeasurer() : null;

  const step = winDur <= 10 ? 2 : winDur <= 20 ? 4 : winDur <= 30 ? 5 : 10;
  const firstTick = Math.ceil(winStart / step) * step;
  const ticks = [];
  for (let t = firstTick; t <= winEnd; t += step) {
    const pct = ((t - winStart) / winDur) * 100;
    ticks.push(
      '<span class="ruler-tick" style="left:' + pct.toFixed(3) + '%">' + clock(t) + '</span>'
    );
  }
  const visibleOverlaps = model.overlapSpans.filter(ov => ov.end > winStart && ov.start < winEnd);
  const rulerOvHtml = visibleOverlaps.map(ov => {
    const left = ((Math.max(ov.start, winStart) - winStart) / winDur) * 100;
    const right = ((Math.min(ov.end, winEnd) - winStart) / winDur) * 100;
    const width = Math.max(0.25, right - left);
    if (width < 2.6) return '';
    const dur = Math.max(0.1, ov.end - ov.start);
    return '<span class="ruler-ov-pill" style="left:' + left.toFixed(3)
      + '%;max-width:' + width.toFixed(3) + '%">∥ ' + dur.toFixed(1) + 's</span>';
  }).join('');
  ruler.innerHTML = ticks.join('') + rulerOvHtml + '<div class="lane-playhead"></div>';

  const ovBandsPlain = visibleOverlaps.map(ov => {
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
      const blockPx = trackPx > 0 ? (trackPx * width) / 100 : 0;
      const info = model.infoByIndex.get(seg.index);
      const hasOv = Boolean(info && info.partners.length);
      const title = spk + ' (' + clockPrecise(seg.start) + '–' + clockPrecise(seg.end) + '): ' + seg.text;
      const content = buildLaneBlockContent(seg, s0, s1, blockPx, info, measurer);
      blocks.push(
        '<div class="lane-block' + (hasOv ? ' has-ov' : '') + content.cls + '"'
        + ' data-index="' + seg.index + '"'
        + ' style="left:' + left.toFixed(3) + '%;width:' + width.toFixed(3) + '%;'
        + '--spk-color:' + c.fg + ';--spk-bg:' + c.bg + ';--spk-bg-hi:' + c.bgHi + '"'
        + ' title="' + html(title) + '">' + content.inner + '</div>'
      );
    }
    const isFiltered = filter === 'spk:' + spk;
    return '<div class="lane-row">'
      + '<div class="lane-label' + (isFiltered ? ' filtered' : '') + '"'
      + ' data-speaker="' + html(spk) + '" style="--spk-color:' + c.fg + '"'
      + ' title="Click to filter transcript to ' + html(spk) + '">' + html(spk) + '</div>'
      + '<div class="lane-track">' + ovBandsPlain + blocks.join('')
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
    const isNow = Boolean(seg && time >= seg.start && time < seg.end);
    blk.classList.toggle('now', isNow);
    if (isNow) {
      blk.querySelectorAll('.lane-word').forEach(w => {
        const ws = Number(w.dataset.t);
        const we = Number(w.dataset.e);
        w.classList.toggle('lw-now', time >= ws && time <= Math.max(we, ws + 0.08));
      });
    } else {
      blk.querySelectorAll('.lane-word.lw-now').forEach(w => w.classList.remove('lw-now'));
    }
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
  const jumpTime = Math.max(0, target.start - 0.5);
  lanesWinStart = clampWinStart(target.start - lanesWindow * 0.25);
  renderLanesWindow(true);
  seekAndPlay(jumpTime);
  const firstIdx = Math.min(...target.indices);
  const row = rowByIndex.get(firstIdx);
  if (row && !row.hidden) scrollRowIntoCaptionBox(row, true);
}

// Dual-sub & swimlane controls & drag/click interactions
const dualSubsBox = document.getElementById('dual-subs');
if (dualSubsBox) {
  dualSubsBox.addEventListener('click', event => {
    const w = event.target.closest('.word');
    if (w) seekAndPlay(Number(w.dataset.t));
  });
}
const toggleAutoScrollBtn = document.getElementById('toggle-autoscroll');
if (toggleAutoScrollBtn) {
  toggleAutoScrollBtn.setAttribute('aria-pressed', String(autoScroll));
  toggleAutoScrollBtn.onclick = () => {
    autoScroll = !autoScroll;
    localStorage.setItem('ytarchive.transcript.autoScroll', String(autoScroll));
    toggleAutoScrollBtn.setAttribute('aria-pressed', String(autoScroll));
    if (autoScroll) syncTranscript(true);
  };
}
const toggleDualSubsBtn = document.getElementById('toggle-dual-subs');
if (toggleDualSubsBtn) {
  toggleDualSubsBtn.setAttribute('aria-pressed', String(showDualSubs));
  toggleDualSubsBtn.onclick = () => {
    showDualSubs = !showDualSubs;
    localStorage.setItem('ytarchive.transcript.showDualSubs', String(showDualSubs));
    applyDualOverlayPosition();
  };
}
const overlaySubsBtn = document.getElementById('toggle-overlay-subs');
if (overlaySubsBtn) {
  overlaySubsBtn.onclick = () => {
    dualOverlay = !dualOverlay;
    localStorage.setItem('ytarchive.transcript.dualOverlay', String(dualOverlay));
    applyDualOverlayPosition();
  };
}
const splitViewBtn = document.getElementById('toggle-split-view');
if (splitViewBtn) {
  splitViewBtn.setAttribute('aria-pressed', String(splitView));
  splitViewBtn.onclick = () => {
    splitView = !splitView;
    localStorage.setItem('ytarchive.transcript.splitView', String(splitView));
    applySplitLayout();
  };
}
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

const lanesRulerEl = document.getElementById('lanes-ruler');
if (lanesRulerEl && typeof ResizeObserver !== 'undefined') {
  new ResizeObserver(() => {
    if (model && !lanesHidden && model.speakers.length >= 2) {
      renderLanesWindow(false);
    }
  }).observe(lanesRulerEl);
}

window.addEventListener('resize', () => {
  syncSplitHeight();
  if (model && !lanesHidden && model.speakers.length >= 2) {
    drawMinimap();
    renderLanesWindow(false);
  }
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
    if (autoScroll && activeIndices.length && !document.getElementById('transcript-search')?.value) {
      const firstRow = rowByIndex.get(activeIndices[0]);
      if (firstRow && !firstRow.hidden) scrollRowIntoCaptionBox(firstRow, false);
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
  updateDualSubs(time);
  updateLanesPlayhead();
}

function tickLanes() {
  const playerVideo = document.querySelector('#ytp video');
  if (!playerVideo || playerVideo.paused || playerVideo.ended) {
    rafId = 0;
    return;
  }
  const time = playerVideo.currentTime || 0;
  updateDualSubs(time);
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
  const tracks = () => Array.from(playerVideo.textTracks || []);
  const paintCc = () => {
    const showing = tracks().find(t => t.mode === 'showing');
    toggleCc.textContent = showing
      ? 'CC: ' + (showing.label || 'On')
      : 'Enable video CC';
  };
  toggleCc.onclick = () => {
    const player = window.MediaPlayer && MediaPlayer.get(playerVideo);
    if (player) player.toggleCaptions();
    else {
      const list = tracks();
      const idx = list.findIndex(t => t.mode === 'showing');
      list.forEach(t => { t.mode = 'hidden'; });
      const next = idx + 1;
      if (next < list.length) list[next].mode = 'showing';
    }
    paintCc();
  };
  document.getElementById('ytp').addEventListener('media-player:captions', paintCc);
  paintCc();
}
const coverBurnedCc = document.getElementById('cover-burned-cc');
if (coverBurnedCc && playerVideo) {
  const ytp = document.getElementById('ytp');
  const applyCueLayout = () => {
    const cover = Boolean(coverBurnedCc.checked);
    if (ytp) ytp.classList.toggle('cover-burned-cc', cover);
    for (const track of Array.from(playerVideo.textTracks || [])) {
      if (!track.cues) continue;
      for (const cue of Array.from(track.cues)) {
        if ('snapToLines' in cue) {
          cue.snapToLines = !cover;
          cue.line = cover ? 83 : 'auto';
          cue.size = cover ? 86 : 100;
        }
      }
    }
  };
  coverBurnedCc.checked = localStorage.getItem('ytarchive.player.coverBurnedCc') === 'true';
  coverBurnedCc.onchange = () => {
    localStorage.setItem('ytarchive.player.coverBurnedCc', String(coverBurnedCc.checked));
    applyCueLayout();
  };
  playerVideo.querySelectorAll('track').forEach(el => {
    el.addEventListener('load', applyCueLayout);
  });
  for (const track of Array.from(playerVideo.textTracks || [])) {
    track.addEventListener('cuechange', applyCueLayout);
  }
  applyCueLayout();
}
if (transcriptReady) loadTranscript().catch(err => {
  document.getElementById('transcript-meta').textContent = err.message;
});
