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
async function reget(kind) {
  const ok = confirm(kind === 'video'
    ? 'Download this video from ' + site + ' again and rebuild its shots and framesheet?'
    : 'Rebuild the shot images and framesheet from the local video?');
  if (!ok) return;
  const png = document.getElementById('reget-png');
  const video = document.getElementById('reget-video');
  const status = document.getElementById('reget-status');
  png.disabled = video.disabled = true;
  status.className = '';
  status.textContent = 'starting…';
  const body = kind === 'video'
    ? {url: vid, force_video: true, force_shots: true}
    : {url: vid, force_shots: true};
  try {
    const data = await post('/api/get', body);
    await watchJob(data.job_id, status);
  } catch (err) {
    status.className = 'error';
    status.textContent = err.message;
    png.disabled = video.disabled = false;
  }
}
document.getElementById('reget-png').onclick = () => reget('png');
document.getElementById('reget-video').onclick = () => reget('video');
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
document.getElementById('open-transcribe').onclick = () => transcribeDialog.showModal();
document.getElementById('cancel-transcribe').onclick = () => transcribeDialog.close();
document.getElementById('default-transcribe').onclick = () => startTranscription({
  model: 'large-v3', language: '', beam_size: 5, vad_filter: true,
});
document.getElementById('configured-transcribe').onclick = () => startTranscription({
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
  setTranscribeProgress(0, 'queueing local Whisper…');
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

let transcript = null;
let activeSegment = -1;
async function loadTranscript() {
  const res = await fetch('/api/transcript/' + encodeURIComponent(vid));
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  transcript = data;
  paintTranscript();
  paintTranscriptSummary();
  paintSpeakerOptions();
}

function paintTranscriptSummary() {
  if (!transcript) return;
  const probability = Math.round((transcript.language_probability || 0) * 100);
  document.getElementById('transcript-meta').textContent =
    transcript.segments.length + ' spoken segments · '
    + (transcript.language || 'unknown language')
    + (probability ? ' ' + probability + '%' : '') + ' · ' + transcript.model;
}

function segmentHtml(segment, matched, hidden) {
  const words = (segment.words || []).length
    ? segment.words.map((word, index) => {
        const gap = index && !/^[,.;:!?)]/.test(word.word) ? ' ' : '';
        return gap + '<span class="word" data-t="'
          + Number(word.start || segment.start) + '">' + html(word.word) + '</span>';
      }).join('')
    : html(segment.text);
  return '<div class="segment' + (matched ? ' match' : '') + '"'
    + (hidden ? ' hidden' : '') + ' data-index="' + segment.index + '" data-start="'
    + Number(segment.start) + '" data-end="' + Number(segment.end) + '">'
    + '<span class="segment-time">' + clock(segment.start) + '</span>'
    + '<button type="button" class="segment-speaker" title="assign or group speaker: '
    + html(segment.speaker || 'Unassigned') + '">' + html(segment.speaker || 'Unassigned') + '</button>'
    + '<div class="segment-text">' + words + '</div></div>';
}

function paintTranscript() {
  const box = document.getElementById('caption-box');
  if (!box || !transcript) return;
  const query = document.getElementById('transcript-search').value.trim().toLowerCase();
  let shown = 0;
  box.innerHTML = transcript.segments.map(segment => {
    const match = !query || segment.text.toLowerCase().includes(query)
      || (segment.speaker || '').toLowerCase().includes(query);
    if (match) shown += 1;
    return segmentHtml(segment, Boolean(query && match), !match);
  }).join('') || '<div class="transcript-empty">No spoken words found.</div>';
  if (query) {
    document.getElementById('transcript-meta').textContent =
      shown + ' matching segment' + (shown === 1 ? '' : 's');
  } else paintTranscriptSummary();
  bindTranscriptRows();
  activeSegment = -1;
  syncTranscript();
}

function bindTranscriptRows() {
  const playerVideo = document.querySelector('#ytp video');
  document.querySelectorAll('.segment').forEach(row => {
    row.addEventListener('click', event => {
      if (event.target.closest('.segment-speaker')) return;
      const word = event.target.closest('.word');
      const time = word ? Number(word.dataset.t) : Number(row.dataset.start);
      if (playerVideo) {
        playerVideo.currentTime = time;
        playerVideo.play();
      }
    });
    row.querySelector('.segment-speaker').addEventListener('click', async event => {
      event.stopPropagation();
      const index = Number(row.dataset.index);
      const segment = transcript.segments.find(item => item.index === index);
      const current = segment ? segment.speaker || '' : '';
      const speaker = prompt(
        'Speaker name. Use the same name on other segments to group them.',
        current
      );
      if (speaker == null) return;
      await changeSegmentSpeaker(index, speaker);
    });
  });
}

async function changeSegmentSpeaker(index, speaker) {
  const res = await fetch('/api/transcript/speaker', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({video_id: vid, segment_index: index, speaker}),
  });
  const data = await res.json();
  if (!res.ok) return alert(data.error || res.statusText);
  transcript = data;
  paintTranscript();
  paintSpeakerOptions();
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
  transcript = data;
  document.getElementById('speaker-new').value = '';
  paintTranscript();
  paintSpeakerOptions();
};

function syncTranscript() {
  const playerVideo = document.querySelector('#ytp video');
  if (!playerVideo || !transcript) return;
  const time = playerVideo.currentTime || 0;
  const index = transcript.segments.findIndex(
    segment => time >= segment.start && time < segment.end
  );
  if (index === activeSegment) return;
  activeSegment = index;
  document.querySelectorAll('.segment').forEach(row => {
    row.classList.toggle('now', Number(row.dataset.index) === index);
  });
  const current = document.querySelector('.segment.now');
  if (current && !document.getElementById('transcript-search').value) {
    current.scrollIntoView({block: 'nearest'});
  }
}

const transcriptSearch = document.getElementById('transcript-search');
if (transcriptSearch) transcriptSearch.addEventListener('input', paintTranscript);
const transcriptView = document.getElementById('transcript-view');
if (transcriptView) {
  transcriptView.value = localStorage.getItem('ytarchive.transcript.view') || 'full';
  transcriptView.onchange = () => {
    localStorage.setItem('ytarchive.transcript.view', transcriptView.value);
    document.getElementById('caption-box').classList.toggle('rolling', transcriptView.value === 'rolling');
    syncTranscript();
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
if (playerVideo) playerVideo.addEventListener('timeupdate', syncTranscript);
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
