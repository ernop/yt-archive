"""The video page: player, transcript, MP3 audio, framesheet and shots."""
from __future__ import annotations

import json
from pathlib import Path

from .. import llm
from ..english import estimate_minutes
from ..paths import (
    all_labeled_path,
    english_ass_path,
    english_dub_path,
    english_has_dub,
    english_is_complete,
    english_srt_path,
    english_transcript_json_path,
    english_transcript_vtt_path,
    find_video_file,
    framesheet_paths,
    media_url,
    lyrics_path,
    mp3_filename,
    read_shots_json,
    shots_dir,
    site_name,
    skips_default_shots,
    soundtrack_path,
    transcript_is_complete,
    transcript_json_path,
    transcript_vtt_path,
    watch_url,
)
from ..settings import load_settings

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
    live_clip = info.get("live_clip")
    if isinstance(live_clip, dict) and live_clip.get("started_at") and live_clip.get("ended_at"):
        stopped = f' ({_esc(live_clip["stopped_by"])})' if live_clip.get("stopped_by") else ""
        facts.append(
            f'<span>live clip <b>{_time_html(live_clip["started_at"])} – '
            f'{_time_html(live_clip["ended_at"])}</b>{stopped}</span>'
        )
    facts.append(f"<span><code>{_esc(vid)}</code></span>")
    facts.append(
        f'<a href="{_esc(watch_url(vid))}" target="_blank" rel="noopener noreferrer">{site_name(vid)}</a>'
    )
    return f'<div class="facts">{"".join(facts)}</div>'


def detail_html(
    info: dict,
    data_dir: Path,
    job: dict | None = None,
    *,
    english_job: dict | None = None,
) -> bytes:
    vid = info["video_id"]
    title = info.get("title") or vid
    duration = info.get("duration")
    video = find_video_file(data_dir, vid)
    transcript_file = transcript_json_path(data_dir, vid)
    captions_file = transcript_vtt_path(data_dir, vid)
    has_transcript = transcript_is_complete(data_dir, vid)
    transcript_language = "und"
    transcript_engine = ""
    if has_transcript:
        try:
            transcript_doc = json.loads(transcript_file.read_text(encoding="utf-8"))
            transcript_language = transcript_doc.get("language") or "und"
            transcript_engine = str(transcript_doc.get("engine") or "")
        except (OSError, json.JSONDecodeError):
            has_transcript = False
    english_file = english_transcript_json_path(data_dir, vid)
    english_vtt = english_transcript_vtt_path(data_dir, vid)
    english_ass = english_ass_path(data_dir, vid)
    english_srt = english_srt_path(data_dir, vid)
    english_dub = english_dub_path(data_dir, vid)
    has_english = english_is_complete(data_dir, vid)
    if has_english:
        try:
            json.loads(english_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            has_english = False
    has_english_dub = english_has_dub(data_dir, vid)
    en_cfg = load_settings()["english"]
    en_config_error = ""
    try:
        llm.check_settings(en_cfg)
    except (ValueError, llm.LLMError) as exc:
        en_config_error = str(exc)
    est_with_dub = estimate_minutes(duration, include_dub=True, has_transcript=has_transcript)
    est_no_dub = estimate_minutes(duration, include_dub=False, has_transcript=has_transcript)
    sheets = framesheet_paths(data_dir, vid)
    long_video = skips_default_shots(duration)
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
        audio_tracks_attr = ""
        if has_english_dub:
            tracks_json = json.dumps(
                [{"src": media_url(data_dir, english_dub), "label": "English dub"}],
                separators=(",", ":"),
            )
            audio_tracks_attr = (
                f' data-audio-tracks="{_esc(tracks_json)}" data-original-audio-label="Original"'
            )
        caption_tracks = []
        if has_english:
            caption_tracks.append(
                f'<track kind="captions" srclang="en" label="English" '
                f'src="{_esc(media_url(data_dir, english_vtt))}">'
            )
        if has_transcript:
            orig_label = (
                f"Original ({transcript_language})"
                if has_english and transcript_language != "und"
                else ("Original" if has_english else "Local transcript")
            )
            caption_tracks.append(
                f'<track kind="captions" srclang="{_esc(transcript_language)}" '
                f'label="{_esc(orig_label)}" src="{_esc(media_url(data_dir, captions_file))}">'
            )
        captions_track = "".join(caption_tracks)
        help_bits = []
        if kept:
            help_bits.append("click a shot to play from it, then PageUp and PageDown step through shots")
        if has_english_dub:
            help_bits.append("B switches audio")
        if has_english or has_transcript:
            help_bits.append("C cycles captions")
        help_bits.extend(["S saves this frame next to the MP3", "T widens the page"])
        if kept:
            help_bits.append("shift-click a shot to open its PNG")
        help_bits.append("? lists every key")
        player_help = " · ".join(help_bits)
        cover_toggle = (
            '<div class="player-extras">'
            '<label class="inline-check"><input type="checkbox" id="cover-burned-cc"> '
            "Cover burned-in captions</label></div>"
            if (has_english or has_transcript)
            else ""
        )
        dual_stage_html = (
            """
<section class="dual-stage" id="dual-stage" hidden>
  <div class="dual-subs" id="dual-subs" aria-live="polite"></div>
  <div class="speaker-lanes" id="speaker-lanes">
    <div class="lanes-head">
      <span class="lanes-title">Speaker overlap timeline</span>
      <span class="lanes-status" id="lanes-status"></span>
      <div class="lanes-tools">
        <button type="button" id="prev-overlap" title="Jump to previous overlapping turn">◀ Prev overlap</button>
        <button type="button" id="next-overlap" title="Jump to next overlapping turn">Next overlap ▶</button>
        <label>Window
          <select id="lanes-window">
            <option value="10">10s</option>
            <option value="20" selected>20s</option>
            <option value="30">30s</option>
            <option value="60">60s</option>
          </select>
        </label>
        <button type="button" id="lanes-follow" aria-pressed="true" title="Keep timeline synced to playback">Follow</button>
        <button type="button" id="toggle-dual-subs" aria-pressed="true" title="Show or hide live dual-color subtitle rows">Live subs</button>
        <button type="button" id="toggle-overlay-subs" aria-pressed="false" title="Show dual-color speaker subtitles over the video frame">Overlay on video</button>
        <button type="button" id="toggle-split-view" aria-pressed="true" title="Side-by-side video and transcript with full-width timeline below">Split view</button>
      </div>
    </div>
    <div class="lanes-stage" id="lanes-stage">
      <div class="lanes-ruler-row">
        <div class="lanes-gutter"></div>
        <div class="lanes-ruler" id="lanes-ruler"></div>
      </div>
      <div class="lanes-body" id="lanes-body"></div>
    </div>
    <div class="lanes-minimap-wrap" id="lanes-minimap-wrap" title="Click or drag to jump anywhere in the video">
      <canvas class="lanes-minimap" id="lanes-minimap" height="24"></canvas>
      <div class="minimap-viewport" id="minimap-viewport"></div>
      <div class="minimap-playhead" id="minimap-playhead"></div>
    </div>
  </div>
</section>
"""
            if (has_transcript or has_english)
            else ""
        )
        parts.append(f"""
<div class="ytp" id="ytp" data-id="{_esc(vid)}">
  <video controls preload="metadata" src="{_esc(media_url(data_dir, video))}" data-media-player
    data-title="{_esc(title)}"{dur_attr} data-markers="{markers_attr}" data-markers-label="Shot"{audio_tracks_attr}
    data-wide>{captions_track}</video>
</div>
{dual_stage_html}
{cover_toggle}
<p class="meta">{player_help}</p>
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
    english_action = "Rebuild English version…" if has_english else "Make English version…"
    has_any_transcript = has_transcript or has_english
    lang_selector = ""
    if has_english:
        orig_opt = '<option value="orig">Original</option>' if has_transcript else ""
        lang_selector = (
            '<label>Language\n'
            '      <select id="transcript-lang">\n'
            '        <option value="en" selected>English</option>\n'
            f"        {orig_opt}\n"
            '        <option value="both">Side-by-side</option>\n'
            "      </select>\n"
            "    </label>"
        )
    download_links = []
    if has_english:
        download_links.append(
            f'<a href="{_esc(media_url(data_dir, english_vtt))}" download>English VTT</a>'
        )
        if english_ass.is_file():
            download_links.append(
                f'<a href="{_esc(media_url(data_dir, english_ass))}" download>English ASS</a>'
            )
        if english_srt.is_file():
            download_links.append(
                f'<a href="{_esc(media_url(data_dir, english_srt))}" download>English SRT</a>'
            )
    if has_transcript:
        download_links.append(
            f'<a href="{_esc(media_url(data_dir, captions_file))}" download>download WebVTT</a>'
        )
    transcript_ready = (
        f"""
  <div class="transcript-meta" id="transcript-meta">Loading transcript…</div>
  <div class="transcript-controls">
    <input class="transcript-search" id="transcript-search" type="search"
      placeholder="search every spoken word…" autocomplete="off">
    {lang_selector}
    <label id="transcript-filter-wrap" hidden>Filter
      <select id="transcript-filter">
        <option value="all">all segments</option>
      </select>
    </label>
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
    <button type="button" id="toggle-autoscroll" aria-pressed="true" title="Scroll the transcript list as playback moves">Auto-scroll</button>
    <button type="button" id="toggle-lanes" hidden>Hide overlap view</button>
    <button type="button" id="toggle-cc">Enable video CC</button>
    {" ".join(download_links)}
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
        if has_any_transcript
        else ""
    )
    use_mt_default = transcript_engine == "multitalker-parakeet"
    english_buttons = []
    if video and not no_audio:
        english_buttons.append(
            f'<button type="button" id="open-english">{english_action}</button>'
        )
    if has_english and video:
        english_buttons.append(
            '<button type="button" id="export-english-mkv">Download bilingual MKV</button>'
        )
        english_buttons.append(
            '<button type="button" id="open-english-vlc">Open in VLC</button>'
        )
    en_model_desc = f"{en_cfg['provider']} / {en_cfg['model']}"
    en_error_html = (
        f'<p class="dialog-error">{_esc(en_config_error)}</p>' if en_config_error else ""
    )
    en_source_val = "" if transcript_language == "und" else transcript_language
    en_job_id = english_job["job_id"] if english_job else ""
    parts.append(
        f"""
<section class="transcript" id="transcript-panel" data-ready="{str(has_any_transcript).lower()}"
  data-has-transcript="{str(has_transcript).lower()}" data-has-english="{str(has_english).lower()}"
  data-has-dub="{str(has_english_dub).lower()}" data-est-dub="{est_with_dub}"
  data-est-no-dub="{est_no_dub}" data-english-job-id="{_esc(en_job_id)}">
  <h2>Spoken transcript &amp; English</h2>
  <p class="meta">Local speech recognition · optional English captions and multi-speaker voiceover dub.</p>
  <div class="actions">
    <button type="button" id="open-transcribe">{transcript_action}</button>
    {" ".join(english_buttons)}
  </div>
  <div id="english-mkv-status" class="meta"></div>
  <div class="transcript-progress" id="transcript-progress" hidden>
    <span class="transcript-progress-value" id="transcript-progress-value">0%</span>
    <span class="transcript-progress-label" id="transcript-progress-label">queued</span>
    <div class="transcript-progress-track"><div class="transcript-progress-fill" id="transcript-progress-fill"></div></div>
  </div>
  <div class="transcript-progress" id="english-progress" hidden>
    <span class="transcript-progress-value" id="english-progress-value">0%</span>
    <span class="transcript-progress-label" id="english-progress-label">queued</span>
    <div class="transcript-progress-track"><div class="transcript-progress-fill" id="english-progress-fill"></div></div>
  </div>
  {transcript_ready}
</section>
<dialog id="transcribe-dialog">
  <h2>{'Replace transcript' if has_transcript else 'Generate transcript'}</h2>
  <p class="meta">Choose multi-speaker mode for conversations with overlapping talk, or single-stream Whisper for 99 languages. Nothing starts until you confirm.</p>
  <div class="engine-choices" role="radiogroup" aria-label="Transcription mode">
    <label class="engine-choice">
      <input type="radio" name="transcribe-engine" id="engine-multitalker" value="multitalker"{' checked' if use_mt_default else ''}>
      <span><strong>Multi-speaker with overlaps</strong> — identifies up to 8 speakers and simultaneous talk (Nemotron-3 + Multitalker Parakeet Ultra, 25 European languages)</span>
    </label>
    <label class="engine-choice">
      <input type="radio" name="transcribe-engine" id="engine-whisper" value="whisper"{'' if use_mt_default else ' checked'}>
      <span><strong>Single stream (Whisper)</strong> — large-v3 default, 99 languages</span>
    </label>
  </div>
  <div class="fields" id="whisper-fields"{' hidden' if use_mt_default else ''}>
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
    <button type="button" id="configured-transcribe"{' hidden' if use_mt_default else ''}>Use these settings</button>
    <button type="button" class="primary" id="default-transcribe">{'Replace with defaults' if has_transcript else 'Yes — transcribe with defaults'}</button>
  </div>
</dialog>
<dialog id="english-dialog">
  <h2>{'Rebuild English version' if has_english else 'Make English version'}</h2>
  <p class="meta">Translates with <strong>{_esc(en_model_desc)}</strong>{' (reusing existing local transcript)' if has_transcript else ' after local Whisper transcription'}, builds English VTT/ASS/SRT subtitles, and optionally synthesizes a multi-speaker voiceover dub.</p>
  <p class="dialog-estimate" id="english-estimate">Estimated time: ~{est_with_dub} min with voiceover dub (~{est_no_dub} min subtitles only).</p>
  {en_error_html}
  <div class="fields">
    <label><span>Voiceover dub</span>
      <span><input type="checkbox" id="english-include-dub" checked> Include multi-speaker English voiceover dub</span>
    </label>
    <label>Source language
      <input id="english-source-lang" maxlength="3" placeholder="auto-detect, or zh / ja / de" value="{_esc(en_source_val)}">
    </label>
  </div>
  <div class="dialog-actions">
    <button type="button" id="cancel-english">Cancel</button>
    <button type="button" class="primary" id="start-english"{' disabled' if en_config_error else ''}>{'Rebuild English version' if has_english else 'Start English build'}</button>
  </div>
</dialog>
"""
    )
    parts.append(soundtrack_html)
    if video and not sheets:
        if long_video:
            parts.append(
                '<div class="shots-empty">'
                "<h2>Shots</h2>"
                '<p class="meta">Shots are not made by default for videos over 30 minutes.</p>'
                '<div class="row"><button type="button" class="btn" id="make-shots">Make shots</button></div>'
                '<div id="shots-status"></div>'
                "</div>"
            )
        else:
            parts.append('<p class="sheet-label">No framesheet yet — the next job for this video makes one.</p>')
    labeled = all_labeled_path(data_dir, vid)
    if sheets or labeled.is_file():
        total_sheet_bytes = sum(s.stat().st_size for s in sheets if s.is_file())
        if labeled.is_file():
            total_sheet_bytes += labeled.stat().st_size
        huge_sheets = len(sheets) > 1 or total_sheet_bytes > 12 * 1024 * 1024
        count_label = (
            f"Framesheets ({len(sheets)} sheets"
            if len(sheets) > 1
            else ("Framesheet (1 sheet" if len(sheets) == 1 else "Framesheets (detections")
        )
        size_part = f" · {_size(total_sheet_bytes)}" if _size(total_sheet_bytes) else ""
        sheet_bits = [
            f'<details class="sheets-toggle" id="framesheets-section"{"" if huge_sheets else " open"}>'
            f"<summary>{_esc(count_label + size_part + ')')}</summary>"
        ]
        for n, sheet in enumerate(sheets, start=1):
            label = (
                f"Squished framesheet {n}/{len(sheets)}"
                if len(sheets) > 1
                else "Squished framesheet"
            )
            sheet_bits.append(
                f'<p class="sheet-label">{_esc(label)}</p>'
                f'<a href="{_esc(media_url(data_dir, sheet))}" target="_blank" rel="noopener">'
                f'<img class="sheet" src="{_esc(media_url(data_dir, sheet))}" alt="{_esc(label)}"></a>'
            )
        if labeled.is_file():
            sheet_bits.append(
                f'<p class="sheet-label">All detections (kept + dropped)</p>'
                f'<a href="{_esc(media_url(data_dir, labeled))}" target="_blank" rel="noopener">'
                f'<img class="sheet" src="{_esc(media_url(data_dir, labeled))}" alt="all shots labeled"></a>'
            )
        sheet_bits.append("</details>")
        parts.append("".join(sheet_bits))
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
        huge_shots = len(figs) > 120
        parts.append(
            f'<details class="sheets-toggle" id="shots-section"{"" if huge_shots else " open"}>'
            f"<summary>Shot images ({len(figs)} shots) — click to play from that moment</summary>"
            f'<div class="shots">{"".join(figs)}</div></details>'
        )
    if sheets or not long_video:
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
    else:
        parts.append(
            """
<div class="backup">
  <h2>Redo</h2>
  <p>If the video came out wrong, download it again.</p>
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
