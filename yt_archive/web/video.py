"""The video page: player, transcript, MP3 audio, framesheet and shots."""
from __future__ import annotations

import json
from pathlib import Path

from ..paths import (
    all_labeled_path,
    find_video_file,
    framesheet_paths,
    media_url,
    lyrics_path,
    mp3_filename,
    read_shots_json,
    shots_dir,
    site_name,
    soundtrack_path,
    transcript_is_complete,
    transcript_json_path,
    transcript_vtt_path,
    watch_url,
)

from .html import (
    _clock,
    _creator_url,
    _esc,
    _media_player_includes,
    _page,
    _size,
    _static_url,
    _time_html,
    _upload_date,
)


def _facts_html(info: dict, creator_link: str, shots: int) -> str:
    """The video's key facts on one line, values first in weight (docs/browsing.md)."""
    vid = info["video_id"]
    facts = [creator_link]
    if _clock(info.get("duration")):
        facts.append(f'<span><b>{_esc(_clock(info.get("duration")))}</b></span>')
    if info.get("width") and info.get("height"):
        extra = " · ".join(
            str(part) for part in (
                f'{round(float(info["fps"]))} fps' if info.get("fps") else "",
                str(info.get("vcodec") or "").split(".")[0],
            ) if part
        )
        facts.append(f'<span><b>{int(info["width"])}×{int(info["height"])}</b>{_esc(extra)}</span>')
    if _size(info.get("file_size")):
        facts.append(f'<span><b>{_esc(_size(info.get("file_size")))}</b></span>')
    if shots:
        facts.append(f"<span><b>{shots}</b>shots</span>")
    if info.get("upload_date"):
        facts.append(f'<span>uploaded <b>{_esc(_upload_date(info["upload_date"]))}</b></span>')
    if info.get("downloaded_at"):
        facts.append(f'<span>got <b>{_time_html(info["downloaded_at"])}</b></span>')
    facts.append(f"<span><code>{_esc(vid)}</code></span>")
    facts.append(
        f'<a href="{_esc(watch_url(vid))}" target="_blank" rel="noopener noreferrer">{site_name(vid)}</a>'
    )
    return f'<div class="facts">{"".join(facts)}</div>'


def detail_html(info: dict, data_dir: Path, job: dict | None = None) -> bytes:
    vid = info["video_id"]
    title = info.get("title") or vid
    duration = info.get("duration")
    video = find_video_file(data_dir, vid)
    transcript_file = transcript_json_path(data_dir, vid)
    captions_file = transcript_vtt_path(data_dir, vid)
    has_transcript = transcript_is_complete(data_dir, vid)
    transcript_language = "und"
    if has_transcript:
        try:
            transcript_language = (
                json.loads(transcript_file.read_text(encoding="utf-8")).get(
                    "language"
                )
                or "und"
            )
        except (OSError, json.JSONDecodeError):
            has_transcript = False
    sheets = framesheet_paths(data_dir, vid)
    shot_recs = (read_shots_json(data_dir, vid) or {}).get("shots", [])
    kept = [r for r in shot_recs if r.get("file") and r.get("kept") is not False]
    # Each kept shot's start is a marker on the player's scale; its id is the shot image's file,
    # so the page can light that image while the shot plays.
    shot_markers = [
        {"t": r["t0"], "label": f"Shot {Path(r['file']).stem}", "id": r["file"]}
        for r in kept
        if r.get("t0") is not None
    ]
    player_head, player_tail = _media_player_includes()
    creator_url = _creator_url(info)
    creator_name = _esc(info.get("channel") or "unknown uploader")
    creator_link = (
        f'<a class="creator-link" href="{_esc(creator_url)}">{creator_name}</a>'
        if creator_url
        else creator_name
    )
    parts = [
        '<header><div><a href="/">← archive</a></div>',
        f"<h1>{_esc(title)}</h1>",
        _facts_html(info, creator_link, len(kept)),
    ]
    if job:
        doing = job.get("message") or ("waiting in the queue" if job.get("status") == "queued" else job.get("phase"))
        parts.append(
            f'<div class="banner"><strong>Being updated now</strong>{_esc(doing)} · '
            'this page shows the files as they are; reload when the '
            '<a href="/#queue">download queue</a> says it is done.</div>'
        )
    if info.get("description"):
        parts.append(
            '<details class="description"><summary>Description</summary>'
            f'<div class="text">{_esc(info["description"])}</div></details>'
        )
    parts.append("</header><main>")
    if video:
        markers_attr = _esc(json.dumps(shot_markers, separators=(",", ":")))
        dur_attr = f' data-duration="{duration}"' if duration else ""
        captions_track = (
            f'<track kind="captions" srclang="{_esc(transcript_language)}" label="Local transcript" '
            f'src="{_esc(media_url(data_dir, captions_file))}">'
            if has_transcript
            else ""
        )
        parts.append(f"""
<div class="ytp" id="ytp" data-id="{_esc(vid)}">
  <video controls preload="metadata" src="{_esc(media_url(data_dir, video))}" data-media-player
    data-title="{_esc(title)}"{dur_attr} data-markers="{markers_attr}" data-markers-label="Shot"
    data-wide>{captions_track}</video>
</div>
<p class="meta">click a shot to play from it, then PageUp and PageDown step through shots · S saves this frame next to the MP3 · T widens the page ·
shift-click a shot to open its PNG · ? lists every key</p>
""")
    mp3 = soundtrack_path(data_dir, vid)
    soundtrack_bits = ['<div class="soundtrack">']
    if mp3.is_file():
        mp3_name = mp3_filename(vid, info.get("title") or "", info.get("channel") or "")
        lrc = lyrics_path(data_dir, vid)
        lyrics_link = (
            f' · <a href="{_esc(media_url(data_dir, lrc))}" download="{_esc(mp3_name.removesuffix(".mp3"))}.lrc">'
            'download lyrics</a>'
            if lrc.is_file() else ""
        )
        soundtrack_bits.append(
            f'<h2>MP3 audio</h2>'
            f'<p class="meta"><a href="{_esc(media_url(data_dir, mp3))}" download="{_esc(mp3_name)}">'
            f'download MP3 audio</a>{lyrics_link}</p>'
            f'<audio controls preload="metadata" src="{_esc(media_url(data_dir, mp3))}" data-media-player '
            f'data-title="{_esc(f"MP3 audio: {title}")}"></audio>'
        )
    else:
        soundtrack_bits.append('<h2>MP3 audio</h2>')
    no_audio = info.get("audio_stream") is False
    can_dump = video and not mp3.is_file() and not no_audio
    if no_audio and not mp3.is_file():
        soundtrack_bits.append('<p class="meta">This video has no audio track, so there is no MP3.</p>')
    row = ['<div class="row">']
    if can_dump:
        row.append('<button type="button" class="btn" id="dump-audio">Create MP3 audio</button>')
    row.append(
        '<button type="button" class="btn" id="open-mp3-folder">Open MP3 folder</button>'
    )
    row.append("</div>")
    if can_dump:
        row.append('<div id="audio-status"></div>')
    soundtrack_bits.extend(row)
    soundtrack_bits.append("</div>")
    soundtrack_html = "".join(soundtrack_bits)
    transcript_action = "Re-transcribe…" if has_transcript else "Transcribe audio…"
    transcript_ready = (
        f"""
  <div class="transcript-meta" id="transcript-meta">Loading transcript…</div>
  <div class="transcript-controls">
    <input class="transcript-search" id="transcript-search" type="search"
      placeholder="search every spoken word…" autocomplete="off">
    <label>View
      <select id="transcript-view">
        <option value="full">full transcript</option>
        <option value="rolling">current caption only</option>
      </select>
    </label>
    <label>Text size
      <select id="transcript-size">
        <option value="small">small</option>
        <option value="medium" selected>medium</option>
        <option value="large">large</option>
      </select>
    </label>
    <button type="button" id="toggle-cc">Enable video CC</button>
    <a href="{_esc(media_url(data_dir, captions_file))}" download>download WebVTT</a>
  </div>
  <div class="caption-box size-medium" id="caption-box" aria-live="polite"></div>
  <div class="speaker-tools">
    <strong>Speaker groups</strong>
    <label>Rename
      <select id="speaker-old"><option value="">choose speaker</option></select>
    </label>
    <label>to
      <input id="speaker-new" maxlength="80" placeholder="name">
    </label>
    <button type="button" id="rename-speaker">Rename/group</button>
    <span class="meta">Click a segment’s speaker label to assign it.</span>
  </div>
"""
        if has_transcript
        else ""
    )
    parts.append(
        f"""
<section class="transcript" id="transcript-panel" data-ready="{str(has_transcript).lower()}">
  <h2>Spoken transcript</h2>
  <p class="meta">Local Whisper · speech only; sound-effect descriptions are not mixed into spoken words.</p>
  <div class="actions">
    <button type="button" id="open-transcribe">{transcript_action}</button>
  </div>
  <div class="transcript-progress" id="transcript-progress" hidden>
    <span class="transcript-progress-value" id="transcript-progress-value">0%</span>
    <span class="transcript-progress-label" id="transcript-progress-label">queued</span>
    <div class="transcript-progress-track"><div class="transcript-progress-fill" id="transcript-progress-fill"></div></div>
  </div>
  {transcript_ready}
</section>
<dialog id="transcribe-dialog">
  <h2>{'Replace transcript' if has_transcript else 'Generate transcript'}</h2>
  <p class="meta">The default uses the highest-quality Whisper model this machine can run. Nothing starts until you confirm.</p>
  <div class="fields">
    <label>Model
      <select id="whisper-model">
        <option value="large-v3" selected>large-v3 — highest quality</option>
        <option value="large-v3-turbo">large-v3-turbo — faster</option>
        <option value="distil-large-v3">distil-large-v3</option>
        <option value="medium">medium</option>
        <option value="small">small</option>
      </select>
    </label>
    <label>Language
      <input id="whisper-language" maxlength="3" placeholder="auto-detect, or en / fi / de">
    </label>
    <label>Beam size
      <select id="whisper-beam">
        <option value="1">1 — fastest</option>
        <option value="3">3</option>
        <option value="5" selected>5 — default</option>
        <option value="10">10 — thorough</option>
      </select>
    </label>
    <label><span>Speech filtering</span>
      <span><input type="checkbox" id="whisper-vad" checked> skip silence/non-speech</span>
    </label>
  </div>
  <div class="dialog-actions">
    <button type="button" id="cancel-transcribe">Cancel</button>
    <button type="button" id="configured-transcribe">Use these settings</button>
    <button type="button" class="primary" id="default-transcribe">{'Replace with defaults' if has_transcript else 'Yes — transcribe with defaults'}</button>
  </div>
</dialog>
"""
    )
    parts.append(soundtrack_html)
    if video and not sheets:
        parts.append('<p class="sheet-label">No framesheet yet — the next job for this video makes one.</p>')
    for n, sheet in enumerate(sheets, start=1):
        label = (
            f"Squished framesheet {n}/{len(sheets)}"
            if len(sheets) > 1
            else "Squished framesheet"
        )
        parts.append(
            f'<p class="sheet-label">{_esc(label)}</p>'
            f'<a href="{_esc(media_url(data_dir, sheet))}" target="_blank" rel="noopener">'
            f'<img class="sheet" src="{_esc(media_url(data_dir, sheet))}" alt="{_esc(label)}"></a>'
        )
    labeled = all_labeled_path(data_dir, vid)
    if labeled.is_file():
        parts.append(
            f'<p class="sheet-label">All detections (kept + dropped)</p>'
            f'<a href="{_esc(media_url(data_dir, labeled))}" target="_blank" rel="noopener">'
            f'<img class="sheet" src="{_esc(media_url(data_dir, labeled))}" alt="all shots labeled"></a>'
        )
    times = {r["file"]: r for r in kept}
    figs = []
    for shot in sorted(shots_dir(data_dir, vid).glob("*.png")):
        rec = times.get(shot.name) or {}
        t0 = rec.get("t0")
        sample = rec.get("sample", t0)
        cap = shot.stem + (f" @ {sample:.1f}s" if sample is not None else "")
        data_t = f' data-t="{sample}"' if sample is not None else ""
        figs.append(
            f'<figure data-file="{_esc(shot.name)}"{data_t}>'
            f'<a href="{_esc(media_url(data_dir, shot))}">'
            f'<img src="{_esc(media_url(data_dir, shot))}" alt="{shot.stem}"></a>'
            f"<figcaption>{cap}</figcaption></figure>"
        )
    if figs:
        parts.append(
            f'<p class="sheet-label">Shot images — click to play from that moment</p>'
            f'<div class="shots">{"".join(figs)}</div>'
        )
    parts.append(
        """
<div class="backup">
  <h2>Redo</h2>
  <p>If a piece came out wrong: rebuild the shots and framesheet from the local video, or download the video again.</p>
  <button type="button" class="btn" id="reget-png">Rebuild shots</button>
  <button type="button" class="btn" id="reget-video">Download again</button>
  <div id="reget-status"></div>
</div>
"""
    )
    parts.append("</main>")
    return _page(
        title,
        "".join(parts),
        scripts=("local-times.js", "video-page.js"),
        data={"vid": vid, "site": site_name(vid)},
        extra_head=f'{player_head}<link rel="stylesheet" href="{_esc(_static_url("video.css"))}">',
        extra_tail=f'{player_tail}<script src="{_esc(_static_url("video.js"))}"></script>',
    )
