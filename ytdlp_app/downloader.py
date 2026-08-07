"""yt-dlp wrappers used by the GUI.

Each function accepts a `progress` text callback (for log lines) and an
`on_pct` numeric callback (for the per-job progress bar), plus an optional
`cancel_event`. The functions are blocking — call them from a worker thread
managed by `jobs.JobQueue`.
"""

from __future__ import annotations

import threading
import time
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import yt_dlp

from .metadata.itunes import ITunesTrack, search_track
from .metadata.parse import parse_youtube_track, sanitize_filename
from .rate_limit import guard_ytdlp_call
from .runtime import apply_ytdlp_runtime_opts, ffmpeg_dir
from .search import _ytdlp_opts_base


ProgressFn = Callable[[str], None]
PctFn = Callable[[float, str], None]

# yt-dlp's progress hook fires many times per second on fast connections.
# Throttle UI updates to avoid drowning the Tk event loop.
_MIN_PROGRESS_INTERVAL_S = 0.25


@dataclass
class DownloadResult:
    success: bool
    errors: int = 0
    message: str = ""
    output_paths: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.output_paths is None:
            self.output_paths = []


@dataclass
class MusicTrackInfo:
    """YouTube metadata captured during a music download."""

    title: str = ""
    uploader: str = ""
    parsed_artist: str = ""
    parsed_title: str = ""
    duration_s: int | None = None
    thumbnail_url: str | None = None
    itunes_match: ITunesTrack | None = None


@dataclass
class MusicDownloadResult:
    success: bool
    output_paths: list[str] = None  # type: ignore[assignment]
    track_infos: list[MusicTrackInfo] = None  # type: ignore[assignment]
    errors: int = 0
    message: str = ""

    def __post_init__(self) -> None:
        if self.output_paths is None:
            self.output_paths = []
        if self.track_infos is None:
            self.track_infos = []


def _shared_opts(out_dir: str, cookies_path: str | None, *, verbose: bool = False) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "outtmpl": str(Path(out_dir) / "%(title)s.%(ext)s"),
        "ignoreerrors": True,
        "retries": 3,
        "socket_timeout": 30,
        "quiet": not verbose,
        "no_warnings": False,
        "verbose": bool(verbose),
    }
    apply_ytdlp_runtime_opts(opts)
    if cookies_path and Path(cookies_path).is_file():
        opts["cookiefile"] = cookies_path
    return opts


class _PipeLogger:
    """Routes yt-dlp's logger output through a progress text callback."""

    def __init__(self, progress: ProgressFn) -> None:
        self._progress = progress

    def debug(self, msg: str) -> None:
        if msg and not msg.startswith("[debug] "):
            self._progress(msg)

    def info(self, msg: str) -> None:
        if msg:
            self._progress(msg)

    def warning(self, msg: str) -> None:
        self._progress(f"WARN: {msg}")

    def error(self, msg: str) -> None:
        self._progress(f"ERROR: {msg}")


class _Cancelled(Exception):
    """Internal sentinel raised from progress hooks to abort yt-dlp."""


def _resolve_audio_path(path: str) -> str:
    """Return the final .mp3 path after FFmpegExtractAudio deletes the source."""
    p = Path(path)
    if p.is_file() and p.suffix.lower() == ".mp3":
        return str(p)
    mp3 = p.with_suffix(".mp3")
    if mp3.is_file():
        return str(mp3)
    if p.parent.is_dir():
        matches = sorted(p.parent.glob(f"{p.stem}*.mp3"))
        if matches:
            return str(matches[0])
    return str(mp3 if mp3.suffix.lower() == ".mp3" else p)


def _attach_progress(
    opts: dict[str, Any],
    progress: ProgressFn,
    on_pct: PctFn | None,
    cancel_event: threading.Event | None,
    *,
    collect_paths: list[str] | None = None,
    collect_infos: list[MusicTrackInfo] | None = None,
) -> None:
    last_emit = {"t": 0.0}

    def _info_from_dict(info: dict[str, Any] | None) -> MusicTrackInfo | None:
        if not isinstance(info, dict):
            return None
        duration = info.get("duration")
        duration_s = int(duration) if isinstance(duration, (int, float)) else None
        thumb = info.get("thumbnail")
        if not isinstance(thumb, str):
            thumbs = info.get("thumbnails")
            if isinstance(thumbs, list) and thumbs:
                last = thumbs[-1]
                if isinstance(last, dict):
                    thumb = last.get("url")
        return MusicTrackInfo(
            title=str(info.get("title") or info.get("fulltitle") or ""),
            uploader=str(info.get("uploader") or info.get("channel") or ""),
            duration_s=duration_s,
            thumbnail_url=thumb if isinstance(thumb, str) else None,
        )

    def _enrich_track_info(info: MusicTrackInfo) -> MusicTrackInfo:
        parsed = parse_youtube_track(info.title, info.uploader)
        info.parsed_artist = parsed.artist
        info.parsed_title = parsed.title
        return info

    def hook(d: dict[str, Any]) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise _Cancelled()

        status = d.get("status")
        if status == "downloading":
            # Always update the percentage (cheap), throttle the text.
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            pct_num = (downloaded / total * 100.0) if total else 0.0

            now = time.monotonic()
            send_text = (now - last_emit["t"]) >= _MIN_PROGRESS_INTERVAL_S
            if send_text:
                last_emit["t"] = now
                pct = (d.get("_percent_str") or "").strip() or f"{pct_num:.1f}%"
                speed = (d.get("_speed_str") or "").strip() or "?/s"
                eta = (d.get("_eta_str") or "").strip() or "?"
                filename = Path(d.get("filename") or "").name
                msg = f"[downloading] {pct} @ {speed}, ETA {eta} — {filename}"
                if on_pct is not None:
                    on_pct(pct_num, msg)
                else:
                    progress(msg)
            elif on_pct is not None:
                on_pct(pct_num, "")  # pct-only update, no log line
        elif status == "finished":
            last_emit["t"] = 0.0
            filename = Path(d.get("filename") or "").name
            msg = f"[done] {filename}"
            if on_pct is not None:
                on_pct(100.0, msg)
            else:
                progress(msg)
            if collect_paths is not None:
                fp = d.get("filename")
                if isinstance(fp, str) and fp:
                    collect_paths.append(fp)
            if collect_infos is not None:
                info = _info_from_dict(d.get("info_dict"))
                if info is not None:
                    collect_infos.append(_enrich_track_info(info))
        elif status == "error":
            msg = f"[error] {d.get('filename') or ''}"
            if on_pct is not None:
                on_pct(0.0, msg)
            else:
                progress(msg)

    def pp_hook(d: dict[str, Any]) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise _Cancelled()
        ppname = d.get("postprocessor") or "postprocess"
        status = d.get("status")
        if status == "started":
            progress(f"[{ppname}] starting...")
        elif status == "finished":
            progress(f"[{ppname}] done")
            if collect_paths is not None:
                fp = d.get("filepath") or d.get("filename")
                if isinstance(fp, str) and fp.lower().endswith(".mp3"):
                    if collect_paths:
                        collect_paths[-1] = fp
                    else:
                        collect_paths.append(fp)

    opts["progress_hooks"] = [hook]
    opts["postprocessor_hooks"] = [pp_hook]
    opts["logger"] = _PipeLogger(progress)


def _run(
    urls: Iterable[str],
    opts: dict[str, Any],
    *,
    progress: ProgressFn,
    on_pct: PctFn | None,
    cancel_event: threading.Event | None,
    collect_paths: list[str] | None = None,
    collect_infos: list[MusicTrackInfo] | None = None,
) -> DownloadResult:
    _attach_progress(
        opts, progress, on_pct, cancel_event,
        collect_paths=collect_paths,
        collect_infos=collect_infos,
    )
    try:
        url_list = list(urls)

        def _do_download() -> int:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.download(url_list)

        code = guard_ytdlp_call(_do_download, progress=progress)
    except _Cancelled:
        return DownloadResult(success=False, message="cancelled")
    except yt_dlp.utils.DownloadError as e:
        return DownloadResult(success=False, message=str(e))
    except Exception as e:  # noqa: BLE001
        return DownloadResult(success=False, message=f"{type(e).__name__}: {e}")
    return DownloadResult(success=code == 0, errors=int(code or 0), output_paths=collect_paths or [])


# ----------------------------- public API --------------------------------- #

def download_audio(
    urls: Iterable[str],
    out_dir: str,
    *,
    cookies_path: str | None = None,
    verbose: bool = False,
    progress: ProgressFn = lambda msg: None,
    on_pct: PctFn | None = None,
    cancel_event: threading.Event | None = None,
) -> DownloadResult:
    """Download audio as MP3 with embedded thumbnail + metadata."""
    opts = _shared_opts(out_dir, cookies_path, verbose=verbose)
    opts.update({
        "format": "bestaudio/best",
        "writethumbnail": True,
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"},
            {"key": "FFmpegMetadata"},
            {"key": "EmbedThumbnail"},
        ],
    })
    return _run(urls, opts, progress=progress, on_pct=on_pct, cancel_event=cancel_event)


def _music_info_from_raw(raw: dict[str, Any]) -> MusicTrackInfo:
    """Build track metadata from a yt-dlp info dict."""
    duration = raw.get("duration")
    duration_s = int(duration) if isinstance(duration, (int, float)) else None
    thumb = raw.get("thumbnail")
    if not isinstance(thumb, str):
        thumbs = raw.get("thumbnails")
        if isinstance(thumbs, list) and thumbs:
            last = thumbs[-1]
            if isinstance(last, dict):
                thumb = last.get("url")
    title = str(raw.get("title") or raw.get("fulltitle") or "")
    uploader = str(raw.get("uploader") or raw.get("channel") or "")
    parsed = parse_youtube_track(title, uploader)
    return MusicTrackInfo(
        title=title,
        uploader=uploader,
        parsed_artist=parsed.artist,
        parsed_title=parsed.title,
        duration_s=duration_s,
        thumbnail_url=thumb if isinstance(thumb, str) else None,
    )


def _clear_existing_track_files(out_dir: Path, stem: str) -> None:
    """Remove prior downloads of the same track (incl. numbered duplicates)."""
    stem_cf = stem.casefold()
    numbered = re.compile(r"^(.+) \(\d+\)$")
    for p in out_dir.iterdir():
        if not p.is_file() or p.suffix.lower() not in (".mp3", ".lrc"):
            continue
        file_stem = p.stem
        m = numbered.match(file_stem)
        if m:
            file_stem = m.group(1)
        if file_stem.casefold() == stem_cf:
            try:
                p.unlink()
            except OSError:
                pass


def _peek_video_info(
    url: str,
    *,
    cookies_path: str | None,
    cancel_event: threading.Event | None,
) -> dict[str, Any]:
    if cancel_event is not None and cancel_event.is_set():
        return {}
    opts = _ytdlp_opts_base(cookies_path=cookies_path)
    opts.update({
        "noplaylist": True,
        "ignoreerrors": True,
        "socket_timeout": 20,
    })
    fd = ffmpeg_dir()
    if fd:
        opts["ffmpeg_location"] = fd
    try:

        def _do_extract() -> dict[str, Any]:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False) or {}
            return info if isinstance(info, dict) else {}

        return guard_ytdlp_call(_do_extract, progress=lambda msg: None)
    except yt_dlp.utils.DownloadError:
        return {}
    except Exception:  # noqa: BLE001
        return {}


def download_music(
    urls: Iterable[str],
    out_dir: str,
    *,
    cookies_path: str | None = None,
    verbose: bool = False,
    progress: ProgressFn = lambda msg: None,
    on_pct: PctFn | None = None,
    cancel_event: threading.Event | None = None,
    prefer_explicit: bool = True,
    title_hint: str | None = None,
    uploader_hint: str | None = None,
    duration_hint: int | None = None,
    thumbnail_hint: str | None = None,
    defer_itunes: bool = False,
) -> MusicDownloadResult:
    """Download audio as MP3 named by parsed track title.

    Cover art and full metadata are applied in the post-download step via
    iTunes (YouTube thumbnail as fallback).

    When `title_hint` is set, skip the pre-download yt-dlp peek. When
    `defer_itunes` is True, skip the pre-download iTunes lookup too so
    bytes start flowing immediately (postprocess still tags the file).
    """
    output_paths: list[str] = []
    track_infos: list[MusicTrackInfo] = []
    any_success = True
    total_errors = 0
    last_message = ""

    for url in urls:
        if cancel_event is not None and cancel_event.is_set():
            return MusicDownloadResult(success=False, message="cancelled")

        raw_info: dict[str, Any] = {}
        if title_hint:
            raw_title = title_hint
            uploader = uploader_hint or ""
            duration_s = duration_hint
            progress("[music] using search metadata — starting download…")
        else:
            raw_info = _peek_video_info(url, cookies_path=cookies_path,
                                        cancel_event=cancel_event)
            raw_title = str(raw_info.get("title") or raw_info.get("fulltitle") or "track")
            uploader = str(raw_info.get("uploader") or raw_info.get("channel") or "")
            duration = raw_info.get("duration")
            duration_s = int(duration) if isinstance(duration, (int, float)) else None

        parsed = parse_youtube_track(raw_title, uploader)
        itunes = None
        if not defer_itunes:
            itunes = search_track(
                parsed.artist, parsed.title,
                duration_s=duration_s,
                prefer_explicit=prefer_explicit,
            )
        filename = sanitize_filename(
            itunes.title if itunes else (parsed.title or raw_title),
        )
        _clear_existing_track_files(Path(out_dir), filename)

        per_paths: list[str] = []
        per_infos: list[MusicTrackInfo] = []
        opts = _shared_opts(out_dir, cookies_path, verbose=verbose)
        opts.update({
            "format": "bestaudio/best",
            "overwrites": True,
            "outtmpl": str(Path(out_dir) / f"{filename}.%(ext)s"),
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
                 "preferredquality": "0"},
                {"key": "FFmpegMetadata"},
            ],
        })
        result = _run(
            [url], opts,
            progress=progress,
            on_pct=on_pct,
            cancel_event=cancel_event,
            collect_paths=per_paths,
            collect_infos=per_infos,
        )
        if not result.success:
            any_success = False
            last_message = result.message
        total_errors += result.errors
        for p in per_paths:
            output_paths.append(_resolve_audio_path(p))
        if per_infos:
            for ti in per_infos:
                ti.itunes_match = itunes
                if not ti.title and raw_title:
                    ti.title = raw_title
                if not ti.uploader and uploader:
                    ti.uploader = uploader
                if ti.duration_s is None and duration_s is not None:
                    ti.duration_s = duration_s
                if not ti.thumbnail_url and thumbnail_hint:
                    ti.thumbnail_url = thumbnail_hint
                if not ti.parsed_artist and parsed.artist:
                    ti.parsed_artist = parsed.artist
                if not ti.parsed_title and parsed.title:
                    ti.parsed_title = parsed.title
            track_infos.extend(per_infos)
        elif raw_info:
            ti = _music_info_from_raw(raw_info)
            ti.itunes_match = itunes
            track_infos.append(ti)
        else:
            track_infos.append(MusicTrackInfo(
                title=raw_title,
                uploader=uploader,
                parsed_artist=parsed.artist,
                parsed_title=parsed.title,
                duration_s=duration_s,
                thumbnail_url=thumbnail_hint,
                itunes_match=itunes,
            ))

    return MusicDownloadResult(
        success=any_success and bool(output_paths),
        output_paths=output_paths,
        track_infos=track_infos,
        errors=total_errors,
        message=last_message,
    )


def download_video(
    urls: Iterable[str],
    out_dir: str,
    *,
    cookies_path: str | None = None,
    verbose: bool = False,
    progress: ProgressFn = lambda msg: None,
    on_pct: PctFn | None = None,
    cancel_event: threading.Event | None = None,
) -> DownloadResult:
    """Download video as MP4 (H.264/AAC where possible) with embedded thumbnail.

    The format selector prefers MP4-compatible streams so we typically only
    remux (much faster) instead of re-encoding.
    """
    opts = _shared_opts(out_dir, cookies_path, verbose=verbose)
    opts.update({
        "format": "bv*[vcodec!^=vp9]+ba[ext=m4a]/b[ext=mp4]/b",
        "merge_output_format": "mp4",
        "writethumbnail": True,
        "embedthumbnail": True,
        "postprocessors": [
            {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"},
            {"key": "FFmpegMetadata"},
            {"key": "EmbedThumbnail"},
        ],
    })
    return _run(urls, opts, progress=progress, on_pct=on_pct, cancel_event=cancel_event)


def download_thumbnails_only(
    urls: Iterable[str],
    out_dir: str,
    *,
    cookies_path: str | None = None,
    verbose: bool = False,
    progress: ProgressFn = lambda msg: None,
    on_pct: PctFn | None = None,
    cancel_event: threading.Event | None = None,
) -> DownloadResult:
    """Download just the thumbnail image (converted to JPG)."""
    opts = _shared_opts(out_dir, cookies_path, verbose=verbose)
    opts.update({
        "skip_download": True,
        "writethumbnail": True,
        "postprocessors": [
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg"},
        ],
    })
    return _run(urls, opts, progress=progress, on_pct=on_pct, cancel_event=cancel_event)


def parse_urls(text: str) -> list[str]:
    """Split a user-entered string into URLs. One URL per line; whitespace-only
    lines are dropped. We deliberately do NOT split on commas — some valid
    URLs contain commas in query strings."""
    return [line.strip() for line in (text or "").splitlines() if line.strip()]
