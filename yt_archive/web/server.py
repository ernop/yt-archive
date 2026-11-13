"""The HTTP server: routes, the JSON API, media and static files."""
from __future__ import annotations

import hmac
import ipaddress
import json
import mimetypes
import re
import shutil
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .. import db
from ..budget import BudgetExceeded
from ..creators import CreatorBrowser, browse_url, playlist_browse_url
from ..db import list_creator_videos, normalize_video_sort
from ..jobs import JobQueue
from ..lyrics import MAX_LYRICS_BYTES, store_lyrics
from ..paths import (
    VIDEO_ID_RE,
    bookmarklet_key,
    condensed_dir,
    default_data_dir,
    is_complete_png,
    write_grab_png,
    list_items,
    load_archive_info,
    parse_video_id,
    video_dir,
)
from ..work import DerivedWorkQueue

from .add import add_html
from .creator import browse_creator_html, creator_html
from .home import home_html
from .html import STATIC_DIR, _esc, _page
from .video import detail_html


# Finished files never change at a given URL (multi-image-client rule).
# HTML embeds ?v=mtime-size; reget writes new bytes → new key.
IMMUTABLE = "public, max-age=31536000, immutable"


LIVE = "no-store"


def _is_local_host(value: str) -> bool:
    value = (value or "").strip().lower()
    if value.startswith("["):
        host = value[1:].split("]", 1)[0]
    else:
        host, separator, port = value.rpartition(":")
        if not separator or not port.isdigit():
            host = value
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _byte_range(header: str | None, size: int):
    """(start, end) for a single "bytes=" range, "unsatisfiable", or None to send it all."""
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", (header or "").strip())
    if not match or not any(match.groups()):
        return None
    first, last = match.groups()
    if not first:
        length = int(last)
        if length == 0 or size == 0:
            return "unsatisfiable"
        return max(0, size - length), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or start > end:
        return "unsatisfiable"
    return start, end


def make_handler(data_dir: Path, queue: JobQueue, work_queue: DerivedWorkQueue):
    creators = CreatorBrowser(data_dir, queue)
    key = bookmarklet_key(data_dir).encode("ascii")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            print(f"{self.address_string()} {fmt % args}", flush=True)

        def do_GET(self):
            # A DNS-rebinding page reaches us under its own Host name; it gets nothing.
            if not _is_local_host(self.headers.get("Host") or ""):
                return self._json({"error": "untrusted Host header"}, 403)
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            if path == "/browse":
                return self._bytes(browse_creator_html((parse_qs(parsed.query).get("source") or [""])[0]))
            if path.startswith("/api/creators/"):
                listing = creators.get(path.removeprefix("/api/creators/"))
                if not listing:
                    return self._json({"error": "This list is no longer kept. Browse it again."}, 404)
                return self._json(listing)
            if path == "/":
                params = parse_qs(parsed.query)
                q = (params.get("q") or [""])[0].strip()
                sort = normalize_video_sort((params.get("sort") or ["recent"])[0])
                return self._bytes(
                    home_html(
                        list_items(data_dir, q, sort), q, data_dir, sort,
                        bookmarklet=key.decode("ascii"),
                        health=db.archive_needs_summary(data_dir),
                        busy=frozenset(db.open_video_ids(data_dir)),
                        total=db.count_videos(data_dir),
                        disk_used=db.archive_disk_bytes(data_dir),
                        disk_free=shutil.disk_usage(data_dir).free,
                    )
                )
            if path == "/add":
                params = parse_qs(parsed.query)
                given = (params.get("key") or [""])[0].encode("utf-8")
                authorized = bool(key) and hmac.compare_digest(given, key)
                return self._bytes(
                    add_html(
                        (params.get("url") or [""])[0].strip()[:2048],
                        (params.get("title") or [""])[0].strip()[:300],
                        authorized,
                    ),
                    200 if authorized else 403,
                )
            if path == "/creator":
                params = parse_qs(parsed.query)
                channel_id = (params.get("channel_id") or [""])[0].strip()[:200]
                channel = (params.get("channel") or [""])[0].strip()[:300]
                q = (params.get("q") or [""])[0].strip()
                sort = normalize_video_sort((params.get("sort") or ["recent"])[0])
                all_items = list_creator_videos(
                    data_dir, channel_id=channel_id, channel=channel, sort=sort
                )
                if not all_items:
                    return self._bytes(
                        _page(
                            "creator not found",
                            '<main><p><a href="/">← archive</a></p>'
                            "<h1>Creator not found</h1></main>",
                        ),
                        404,
                    )
                items = (
                    list_creator_videos(
                        data_dir,
                        channel_id=channel_id,
                        channel=channel,
                        query=q,
                        sort=sort,
                    )
                    if q
                    else all_items
                )
                creator_name = all_items[0].get("channel") or channel or channel_id
                return self._bytes(
                    creator_html(
                        items,
                        creator_name=creator_name,
                        channel_id=channel_id,
                        channel=channel,
                        query=q,
                        sort=sort,
                        total_count=len(all_items),
                        data_dir=data_dir,
                        busy=frozenset(db.open_video_ids(data_dir)),
                    )
                )
            if path == "/api/list":
                params = parse_qs(parsed.query)
                q = (params.get("q") or [""])[0].strip()
                sort = normalize_video_sort((params.get("sort") or ["recent"])[0])
                return self._json(list_items(data_dir, q, sort))
            if path == "/api/jobs":
                params = parse_qs(parsed.query)
                try:
                    limit = min(1000, int((params.get("waiting") or [db.WAITING_SHOWN])[0]))
                except ValueError:
                    limit = db.WAITING_SHOWN
                return self._json(db.queue_overview(data_dir, limit))
            if path.startswith("/api/job/"):
                job = queue.get(path.split("/", 3)[-1])
                if not job:
                    return self._json({"error": "unknown job"}, 404)
                return self._json(queue.snapshot(job))
            if path.startswith("/api/work/"):
                job = work_queue.get(path.split("/", 3)[-1])
                if not job:
                    return self._json({"error": "unknown derived job"}, 404)
                return self._json(job)
            if path.startswith("/api/transcript/"):
                from ..db import get_transcript

                try:
                    video_id = parse_video_id(path.split("/", 3)[-1])
                except ValueError:
                    return self._json({"error": "bad id"}, 404)
                transcript = get_transcript(data_dir, video_id)
                if not transcript:
                    return self._json({"error": "no transcript"}, 404)
                return self._json(transcript)
            if path.startswith("/v/"):
                video_id = path[3:].strip("/")
                try:
                    video_id = parse_video_id(video_id)
                except ValueError:
                    return self._json({"error": "bad id"}, 404)
                folder = video_dir(data_dir, video_id)
                if not folder.is_dir():
                    return self._bytes(_page("missing", f"<main><p>No archive for {_esc(video_id)}</p></main>"), 404)
                info = load_archive_info(data_dir, video_id)
                return self._bytes(detail_html(info, data_dir, db.find_open_job(data_dir, video_id)))
            if path.startswith("/media/"):
                return self._media(path[len("/media/"):])
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            self.send_error(404)

        def do_POST(self):
            if not self._trusted_mutation():
                return
            parsed = urlparse(self.path)
            if parsed.path == "/api/creators":
                payload = self._read_json()
                if payload is None:
                    return
                try:
                    if "refresh" in payload and not isinstance(payload["refresh"], bool):
                        raise ValueError("refresh must be a boolean")
                    listing = creators.start(payload.get("source"), refresh=payload.get("refresh", False))
                    return self._json(listing, 202 if listing["status"] == "loading" else 200)
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
                except BudgetExceeded as exc:
                    return self._json({"error": str(exc), "retry_at": exc.free_at}, 429)
            if parsed.path.startswith("/api/creators/") and parsed.path.endswith("/get"):
                payload = self._read_json(max_bytes=1024 * 1024)
                if payload is None:
                    return
                try:
                    if "all" in payload and not isinstance(payload["all"], bool):
                        raise ValueError("all must be a boolean")
                    result = creators.enqueue(
                        parsed.path.removeprefix("/api/creators/").removesuffix("/get"),
                        video_ids=payload.get("video_ids"), all_items=payload.get("all", False),
                    )
                    return self._json(result, 202)
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
            if parsed.path == "/api/update-archive":
                return self._update_archive()
            if parsed.path == "/api/jobs/clear-waiting":
                if self._read_json() is None:
                    return
                return self._json({"removed": db.remove_all_waiting_jobs(data_dir)})
            match = re.fullmatch(r"/api/jobs/([0-9a-f]{1,64})/(remove|dismiss)", parsed.path)
            if match:
                if self._read_json() is None:
                    return
                job_id, action = match.groups()
                done = (
                    db.remove_waiting_job(data_dir, job_id)
                    if action == "remove"
                    else db.dismiss_failed_job(data_dir, job_id)
                )
                if not done:
                    return self._json({"error": f"that job is no longer {'waiting' if action == 'remove' else 'failed'}"}, 409)
                return self._json({"ok": True})
            if parsed.path == "/api/transcribe":
                return self._transcribe()
            if parsed.path == "/api/transcript/speaker":
                return self._set_segment_speaker()
            if parsed.path == "/api/transcript/rename-speaker":
                return self._rename_speaker()
            if parsed.path == "/api/open-folder":
                return self._open_folder()
            if parsed.path == "/api/grab":
                return self._grab(parsed)
            if parsed.path == "/api/lyrics":
                return self._lyrics()
            if parsed.path != "/api/get":
                return self.send_error(404)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                qs = parse_qs(raw.decode("utf-8", errors="replace"))
                payload = {"url": (qs.get("url") or [""])[0]}
            if not isinstance(payload, dict):
                return self._json({"error": "body must be an object"}, 400)
            url = payload.get("url") or payload.get("video_id") or ""
            if not isinstance(url, str):
                return self._json({"error": "url must be text"}, 400)
            url = url.strip()
            force_video = bool(payload.get("force_video"))
            force_shots = bool(payload.get("force_shots"))
            via = payload.get("via")
            try:
                if via is not None:
                    db.check_add_route(via)
                try:
                    parse_video_id(url)
                except ValueError:
                    if force_video or force_shots:
                        raise
                    return self._json({"browse_url": browse_url(url)})
                job = queue.submit(url, force_video=force_video, force_shots=force_shots, via=via)
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            queued = job.get("status") != "complete" and not job.get("existing")
            result = queue.snapshot(job)
            if playlist_browse_url(url):
                result["playlist_browse_url"] = playlist_browse_url(url)
            return self._json(result, 202 if queued else 200)

        def _lyrics(self):
            """A local client's lyrics for a video; null removes them (docs/browsing.md, "Lyrics")."""
            payload = self._read_json(max_bytes=MAX_LYRICS_BYTES + 4096)
            if payload is None:
                return
            try:
                video_id = payload.get("video_id")
                if not isinstance(video_id, str) or not VIDEO_ID_RE.fullmatch(video_id):
                    raise ValueError("video_id must be a YouTube video id")
                if "lrc" not in payload:
                    raise ValueError("lrc is required; null removes the lyrics")
                lrc = payload["lrc"]
                if lrc is not None and (not isinstance(lrc, str) or not lrc.strip()):
                    raise ValueError("lrc must be text or null")
                if lrc is not None and len(lrc.encode("utf-8")) > MAX_LYRICS_BYTES:
                    raise ValueError("lrc is too long")
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            return self._json({"video_id": video_id, "lyrics": store_lyrics(data_dir, video_id, lrc)})

        def _transcribe(self):
            payload = self._read_json()
            if payload is None:
                return
            try:
                video_id = parse_video_id(
                    (payload.get("video_id") or payload.get("url") or "").strip()
                )
                config = payload.get("config") or {}
                if not isinstance(config, dict):
                    raise ValueError("config must be an object")
                job = work_queue.submit_transcription(
                    video_id, config, force=bool(payload.get("force"))
                )
            except (ValueError, OSError) as exc:
                return self._json({"error": str(exc)}, 400)
            return self._json(job, 202 if job["status"] != "done" else 200)

        def _set_segment_speaker(self):
            payload = self._read_json()
            if payload is None:
                return
            try:
                video_id = parse_video_id((payload.get("video_id") or "").strip())
                segment_index = int(payload.get("segment_index"))
                from ..transcribe import update_segment_speaker

                update_segment_speaker(
                    data_dir, video_id, segment_index, payload.get("speaker") or ""
                )
                from ..db import get_transcript

                return self._json(get_transcript(data_dir, video_id))
            except (ValueError, TypeError, OSError, json.JSONDecodeError) as exc:
                return self._json({"error": str(exc)}, 400)

        def _rename_speaker(self):
            payload = self._read_json()
            if payload is None:
                return
            try:
                video_id = parse_video_id((payload.get("video_id") or "").strip())
                from ..transcribe import rename_speaker

                rename_speaker(
                    data_dir,
                    video_id,
                    payload.get("old") or "",
                    payload.get("new") or "",
                )
                from ..db import get_transcript

                return self._json(get_transcript(data_dir, video_id))
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                return self._json({"error": str(exc)}, 400)

        def _update_archive(self):
            """Queue every archived video a plain job would still change (docs/browsing.md)."""
            if self._read_json() is None:
                return
            queued = []
            for video_id in db.archive_needs_summary(data_dir)["videos"]:
                job = queue.submit(video_id)
                if job.get("status") != "complete" and not job.get("existing"):
                    queued.append(video_id)
            return self._json({"queued": len(queued), "video_ids": queued}, 202 if queued else 200)

        def _open_folder(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                return self._json({"error": "bad json"}, 400)
            try:
                video_id = parse_video_id((payload.get("video_id") or payload.get("url") or "").strip())
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            folder = condensed_dir(data_dir, video_id)
            if not folder.is_dir():
                folder = video_dir(data_dir, video_id)
            if not folder.is_dir():
                return self._json({"error": "no archive folder"}, 404)
            try:
                subprocess.Popen(
                    ["xdg-open", str(folder)],
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as exc:
                return self._json({"error": f"could not open folder: {exc}", "path": str(folder)}, 500)
            return self._json({"ok": True, "path": str(folder)})

        def _grab(self, parsed):
            qs = parse_qs(parsed.query)
            try:
                video_id = parse_video_id((qs.get("video_id") or [""])[0].strip())
                t = float((qs.get("t") or ["0"])[0])
            except (ValueError, TypeError) as exc:
                return self._json({"error": str(exc)}, 400)
            if not video_dir(data_dir, video_id).is_dir():
                return self._json({"error": "no archive"}, 404)
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 8 or length > 25 * 1024 * 1024:
                return self._json({"error": "bad frame size"}, 400)
            raw = self.rfile.read(length)
            if not is_complete_png(raw):
                return self._json({"error": "not a complete png"}, 400)
            folder = condensed_dir(data_dir, video_id)
            folder.mkdir(parents=True, exist_ok=True)
            title = load_archive_info(data_dir, video_id).get("title") or video_id
            dest = write_grab_png(folder, t, title, raw)
            return self._json({"ok": True, "name": dest.name})

        def _static(self, rel: str):
            root = STATIC_DIR.resolve()
            full = (root / rel).resolve()
            try:
                full.relative_to(root)
            except ValueError:
                return self.send_error(404)
            if not full.is_file():
                return self.send_error(404)
            ctype = mimetypes.guess_type(full.name)[0] or "application/octet-stream"
            raw = full.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", IMMUTABLE)
            self.end_headers()
            self.wfile.write(raw)

        def _media(self, rel: str):
            root = data_dir.resolve()
            full = (root / rel).resolve()
            try:
                full.relative_to(root)
            except ValueError:
                return self.send_error(404)
            if not full.is_file():
                return self.send_error(404)
            ctype = mimetypes.guess_type(full.name)[0] or "application/octet-stream"
            size = full.stat().st_size
            rng = _byte_range(self.headers.get("Range"), size)
            if rng == "unsatisfiable":
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if rng:
                start, end = rng
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Content-Length", str(end - start + 1))
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", IMMUTABLE)
                self.end_headers()
                with full.open("rb") as fh:
                    fh.seek(start)
                    remaining = end - start + 1
                    while remaining:
                        chunk = fh.read(min(64 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", IMMUTABLE)
            self.end_headers()
            with full.open("rb") as fh:
                while True:
                    chunk = fh.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

        def _json(self, obj, code=200):
            raw = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", LIVE)
            self.end_headers()
            self.wfile.write(raw)

        def _read_json(self, max_bytes=64 * 1024):
            content_type = (self.headers.get("Content-Type") or "").split(";", 1)[
                0
            ].strip().lower()
            if content_type != "application/json":
                self._json({"error": "Content-Type must be application/json"}, 415)
                return None
            raw_length = self.headers.get("Content-Length")
            try:
                length = int(raw_length or 0)
            except ValueError:
                self._json({"error": "invalid Content-Length"}, 400)
                return None
            if length < 0:
                self._json({"error": "invalid Content-Length"}, 400)
                return None
            if length > max_bytes:
                self._json({"error": "request body too large"}, 413)
                return None
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._json({"error": "bad json"}, 400)
                return None
            if not isinstance(payload, dict):
                self._json({"error": "json body must be an object"}, 400)
                return None
            return payload

        def _trusted_mutation(self) -> bool:
            request_host = (self.headers.get("Host") or "").lower()
            if not _is_local_host(request_host):
                self._json({"error": "untrusted Host header"}, 403)
                return False
            if (self.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
                self._json({"error": "cross-site request rejected"}, 403)
                return False
            origin = self.headers.get("Origin")
            if origin:
                origin_host = urlparse(origin).netloc.lower()
                if not origin_host or origin_host != request_host:
                    self._json({"error": "cross-origin request rejected"}, 403)
                    return False
            return True

        def _bytes(self, raw: bytes, code=200):
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", LIVE)
            self.end_headers()
            self.wfile.write(raw)

    return Handler


def serve(data_dir: Path | None = None, host: str = "127.0.0.1", port: int = 8765) -> None:
    data_dir = (data_dir or default_data_dir()).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    from ..db import db_path, rebuild
    from ..download import keep_yt_dlp_current_hourly

    n = rebuild(data_dir)
    queue = JobQueue(data_dir)
    work_queue = DerivedWorkQueue(data_dir)
    keep_yt_dlp_current_hourly(data_dir)
    httpd = ThreadingHTTPServer(
        (host, port), make_handler(data_dir, queue, work_queue)
    )
    print(f"ytarchive  http://{host}:{port}/  data={data_dir}  index={n} ({db_path(data_dir)})", flush=True)
    httpd.serve_forever()
