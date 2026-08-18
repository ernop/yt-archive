"""CLI: get / shots / serve / list."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .download import download
from .framesheet import make_shots
from .paths import default_data_dir, find_video_file, parse_video_id
from .web import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="yt-archive", description="Local YouTube archive + shot images")
    parser.add_argument("--data", type=Path, default=default_data_dir(), help="archive root (default: ./data)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    get_p = sub.add_parser("get", help="download a video and extract shot images")
    get_p.add_argument("target", help="YouTube URL or 11-char id")
    get_p.add_argument("--sim", type=float, default=0.90, help="CLIP drop threshold (default 0.90)")
    get_p.add_argument("--skip-shots", action="store_true", help="download only")

    shots_p = sub.add_parser("shots", help="extract shot images from an already-downloaded video")
    shots_p.add_argument("target", help="YouTube URL or 11-char id")
    shots_p.add_argument("--sim", type=float, default=0.90)

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
        return _shots(data_dir, args.target, args.sim)
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
    print(f"browse: /v/{video_id}")
    return 0


def _shots(data_dir: Path, target: str, sim: float) -> int:
    video_id = parse_video_id(target)
    video = find_video_file(data_dir, video_id)
    if not video:
        raise SystemExit(f"no downloaded video for {video_id} in {data_dir}")
    summary = make_shots(video, data_dir, video_id, sim_threshold=sim, layout="squash")
    print(json.dumps({k: summary[k] for k in ("video_id", "shots_detected", "shots_kept")}, indent=2))
    return 0
