"""CLI: get / shots / audio / transcribe / serve / list."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .audio import dump_soundtrack
from .download import download
from .framesheet import make_shots
from .paths import default_data_dir, find_video_file, parse_video_id
from .transcribe import MODELS, transcribe_soundtrack
from .web import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="yt-archive", description="Local YouTube archive + shot images")
    parser.add_argument("--data", type=Path, default=default_data_dir(), help="archive root (default: ./data)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    get_p = sub.add_parser("get", help="download a video, extract shot images, dump soundtrack")
    get_p.add_argument("target", help="YouTube URL or 11-char id")
    get_p.add_argument("--sim", type=float, default=0.85, help="CLIP last-kept change threshold (default 0.85)")
    get_p.add_argument("--skip-shots", action="store_true", help="download only")

    shots_p = sub.add_parser("shots", help="extract shot images from an already-downloaded video")
    shots_p.add_argument("target", help="YouTube URL or 11-char id")
    shots_p.add_argument("--sim", type=float, default=0.85, help="CLIP last-kept change threshold (default 0.85)")
    shots_p.add_argument(
        "--all-sheet",
        action="store_true",
        help="also write shots_all_labeled.png (every detection, numbered + timestamp)",
    )

    audio_p = sub.add_parser("audio", help="create MP3 audio from an already-downloaded video")
    audio_p.add_argument("target", help="YouTube URL or 11-char id")

    transcribe_p = sub.add_parser(
        "transcribe", help="explicitly run local Whisper on an existing MP3"
    )
    transcribe_p.add_argument("target", help="YouTube URL or 11-char id")
    transcribe_p.add_argument("--model", choices=MODELS, default="large-v3")
    transcribe_p.add_argument("--language", default="", help="ISO code; blank auto-detects")
    transcribe_p.add_argument("--beam-size", type=int, default=5)

    sub.add_parser("list", help="list archived videos")
    sub.add_parser("reindex", help="rebuild the sqlite catalog from data/")

    serve_p = sub.add_parser("serve", help="run the ytarchive web UI")
    serve_p.add_argument("--port", type=int, default=8765)
    serve_p.add_argument("--host", default="127.0.0.1")

    args = parser.parse_args(argv)
    data_dir: Path = args.data.expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)

    if args.cmd == "get":
        return _get(data_dir, args.target, args.sim, args.skip_shots)
    if args.cmd == "shots":
        return _shots(data_dir, args.target, args.sim, args.all_sheet)
    if args.cmd == "audio":
        return _audio(data_dir, args.target)
    if args.cmd == "transcribe":
        return _transcribe(
            data_dir,
            args.target,
            {
                "model": args.model,
                "language": args.language,
                "beam_size": args.beam_size,
                "vad_filter": True,
            },
        )
    if args.cmd == "list":
        from .db import list_videos

        for it in list_videos(data_dir):
            print(f"{it['video_id']}\t{it.get('title') or ''}")
        return 0
    if args.cmd == "reindex":
        from .db import db_path, rebuild

        n = rebuild(data_dir)
        print(f"indexed {n} videos → {db_path(data_dir)}")
        return 0
    if args.cmd == "serve":
        serve(data_dir, host=args.host, port=args.port)
        return 0
    return 2


def _get(data_dir: Path, target: str, sim: float, skip_shots: bool) -> int:
    video_id = parse_video_id(target)
    video = download(video_id, data_dir)
    print(f"video: {video} ({video.stat().st_size / 1e6:.1f} MB)")
    if not skip_shots:
        summary = make_shots(video, data_dir, video_id, sim_threshold=sim, layout="squash")
        print(json.dumps({k: summary[k] for k in ("video_id", "shots_detected", "shots_kept")}, indent=2))
    mp3 = dump_soundtrack(video, data_dir, video_id)
    if mp3:
        print(f"MP3 audio: {mp3} ({mp3.stat().st_size / 1e6:.1f} MB)")
    print(f"browse: /v/{video_id}")
    return 0


def _shots(data_dir: Path, target: str, sim: float, all_sheet: bool = False) -> int:
    video_id = parse_video_id(target)
    video = find_video_file(data_dir, video_id)
    if not video:
        raise SystemExit(f"no downloaded video for {video_id} in {data_dir}")
    summary = make_shots(
        video, data_dir, video_id, sim_threshold=sim, layout="squash", write_all_sheet=all_sheet
    )
    keys = ["video_id", "shots_detected", "shots_kept", "compare", "sample"]
    print(json.dumps({k: summary[k] for k in keys if k in summary}, indent=2))
    return 0


def _audio(data_dir: Path, target: str) -> int:
    video_id = parse_video_id(target)
    video = find_video_file(data_dir, video_id)
    if not video:
        raise SystemExit(f"no downloaded video for {video_id} in {data_dir}")
    mp3 = dump_soundtrack(video, data_dir, video_id)
    if not mp3:
        print("no audio stream")
        return 0
    print(f"MP3 audio: {mp3} ({mp3.stat().st_size / 1e6:.1f} MB)")
    return 0


def _transcribe(data_dir: Path, target: str, config: dict) -> int:
    video_id = parse_video_id(target)
    artifact = transcribe_soundtrack(data_dir, video_id, config)
    print(
        f"transcript: {len(artifact['segments'])} spoken segments, "
        f"language={artifact['language']}, model={artifact['model']}"
    )
    return 0
