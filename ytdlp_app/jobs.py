"""Concurrent job queue for downloads and embed tasks.

A `JobQueue` owns two bounded `ThreadPoolExecutor`s — one for downloads /
postprocess, and a separate pool for search / resolve / match — plus an
in-process pending list. Search never waits behind a download (and vice
versa). Callers `enqueue(...)` a `Job`; the queue runs the appropriate
function from `downloader` / `embed` / `search`. Job state mutations are
delivered to a single listener callback which marshals them to the Tk
main thread.
"""

from __future__ import annotations

import itertools
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from . import apple_music as am
from . import downloader as dl
from . import embed as em
from . import music_postprocess as mp


# ---------------------------- public data model --------------------------- #

# State labels are plain strings so they can be tested + serialized easily.
QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"

# Jobs that must not compete with download workers for slots.
_SEARCH_KINDS = frozenset({
    "search",
    "search_more",
    "resolve",
    "source_resolve",
    "source_match_all",
})
_DEFAULT_SEARCH_WORKERS = 2


@dataclass
class Job:
    """One unit of background work tracked by the JobQueue.

    `kind` controls which function in `downloader` / `embed` will be called.
    `params` is a kind-specific dict (see `JobQueue._run_job`).
    """

    id: int
    kind: str                              # "audio" | "video" | "thumb" | "embed_single" | "embed_folder" | "search"
    label: str                             # user-visible: "MP3: Title — Uploader"
    params: dict[str, Any]                 # function args
    state: str = QUEUED
    progress_pct: float = 0.0              # 0..100 (NaN-safe: clamped)
    progress_msg: str = ""
    error: str = ""
    result: Any = None                     # for search jobs, the list of SearchResult
    cancel_event: threading.Event = field(default_factory=threading.Event)

    @property
    def is_terminal(self) -> bool:
        return self.state in (DONE, FAILED, CANCELLED)

    @property
    def is_active(self) -> bool:
        return self.state in (QUEUED, RUNNING)


# `Listener(job)` is called any time `job` mutates. It is invoked on a worker
# thread; the GUI listener is expected to push the job into a queue for the
# main thread to consume.
Listener = Callable[[Job], None]


# ------------------------------- the queue ------------------------------- #

class JobQueue:
    def __init__(
        self,
        max_parallel: int,
        listener: Listener,
        *,
        max_search_parallel: int = _DEFAULT_SEARCH_WORKERS,
    ) -> None:
        self._max_parallel = max(1, int(max_parallel))
        self._max_search_parallel = max(1, int(max_search_parallel))
        self._listener = listener
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_parallel,
            thread_name_prefix="ytdlp-dl",
        )
        self._search_executor = ThreadPoolExecutor(
            max_workers=self._max_search_parallel,
            thread_name_prefix="ytdlp-search",
        )
        self._id_seq = itertools.count(1)
        self._lock = threading.Lock()
        self._jobs: dict[int, Job] = {}
        self._futures: dict[int, Future] = {}

    # --------- pool configuration --------- #

    def set_max_parallel(self, n: int) -> None:
        """Change download worker count. Search pool is unaffected.
        The old executor finishes its current jobs in the background;
        new download jobs go to the new executor."""
        n = max(1, int(n))
        if n == self._max_parallel:
            return
        old = self._executor
        self._max_parallel = n
        self._executor = ThreadPoolExecutor(
            max_workers=n, thread_name_prefix="ytdlp-dl",
        )
        # Don't wait on old jobs here — they keep running on the old pool.
        old.shutdown(wait=False)

    def _pool_for(self, kind: str) -> ThreadPoolExecutor:
        if kind in _SEARCH_KINDS:
            return self._search_executor
        return self._executor

    # --------- enqueue + cancel --------- #

    def enqueue(self, kind: str, label: str, **params: Any) -> Job:
        with self._lock:
            job = Job(
                id=next(self._id_seq),
                kind=kind,
                label=label,
                params=params,
            )
            self._jobs[job.id] = job
        self._notify(job)
        # submit() may run immediately on a free worker thread.
        fut = self._pool_for(kind).submit(self._run_job, job)
        with self._lock:
            self._futures[job.id] = fut
        return job

    def cancel(self, job_id: int) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None or job.is_terminal:
            return
        job.cancel_event.set()
        if job.state == QUEUED:
            job.state = CANCELLED
            self._notify(job)

    def cancel_all(self) -> None:
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            if job.is_active:
                self.cancel(job.id)

    def shutdown(self, wait: bool = False) -> None:
        self.cancel_all()
        self._executor.shutdown(wait=wait)
        self._search_executor.shutdown(wait=wait)

    # --------- inspection --------- #

    def active(self) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if j.is_active]

    def recent(self, n: int = 10) -> list[Job]:
        """Last `n` terminal jobs, most-recent first."""
        with self._lock:
            terms = [j for j in self._jobs.values() if j.is_terminal]
        terms.sort(key=lambda j: j.id, reverse=True)
        return terms[:n]

    def clear_recent(self) -> None:
        with self._lock:
            self._jobs = {jid: j for jid, j in self._jobs.items() if j.is_active}

    # --------- internal --------- #

    def _notify(self, job: Job) -> None:
        try:
            self._listener(job)
        except Exception:  # noqa: BLE001 — never let listener break worker
            pass

    def _run_job(self, job: Job) -> None:
        if job.cancel_event.is_set():
            job.state = CANCELLED
            self._notify(job)
            return

        job.state = RUNNING
        job.progress_msg = "Starting..."
        self._notify(job)

        try:
            self._dispatch(job)
        except _Cancelled:
            job.state = CANCELLED
            job.progress_msg = "Cancelled."
        except Exception as e:  # noqa: BLE001
            job.state = FAILED
            job.error = f"{type(e).__name__}: {e}"
            job.progress_msg = job.error
        else:
            if job.cancel_event.is_set():
                job.state = CANCELLED
                job.progress_msg = "Cancelled."
            elif job.state != FAILED:
                job.state = DONE
                if not job.progress_msg or job.progress_msg.startswith("Starting"):
                    job.progress_msg = "Done."
        finally:
            self._notify(job)

    def _dispatch(self, job: Job) -> None:
        """Translate Job.kind into a concrete function call."""
        def on_progress(pct: float, msg: str) -> None:
            if job.cancel_event.is_set():
                raise _Cancelled()
            new_pct = _clamp_pct(pct)
            pct_changed = abs(new_pct - job.progress_pct) >= 1.0
            job.progress_pct = new_pct
            if msg:
                if msg != job.progress_msg:
                    job.progress_msg = msg
                    self._notify(job)
            elif pct_changed:
                self._notify(job)

        def log(msg: str) -> None:
            if job.cancel_event.is_set():
                raise _Cancelled()
            if msg != job.progress_msg:
                job.progress_msg = msg
                self._notify(job)

        params = job.params
        cookies = params.get("cookies_path") or None
        verbose = bool(params.get("verbose", False))

        if job.kind == "audio":
            result = dl.download_audio(
                [params["url"]], params["output_dir"],
                cookies_path=cookies,
                verbose=verbose,
                progress=log,
                on_pct=on_progress,
                cancel_event=job.cancel_event,
            )
            if not result.success:
                job.state = FAILED
                job.error = result.message or "see log"

        elif job.kind == "video":
            result = dl.download_video(
                [params["url"]], params["output_dir"],
                cookies_path=cookies,
                verbose=verbose,
                progress=log,
                on_pct=on_progress,
                cancel_event=job.cancel_event,
            )
            if not result.success:
                job.state = FAILED
                job.error = result.message or "see log"

        elif job.kind == "thumb":
            result = dl.download_thumbnails_only(
                [params["url"]], params["output_dir"],
                cookies_path=cookies,
                verbose=verbose,
                progress=log,
                on_pct=on_progress,
                cancel_event=job.cancel_event,
            )
            if not result.success:
                job.state = FAILED
                job.error = result.message or "see log"

        elif job.kind == "music":
            from . import search as se

            url = params["url"]
            # User-picked search results already chose a URL — rematching
            # only delays the download. Spotify/auto matches still rematch
            # when Prefer audio is on.
            skip_rematch = bool(params.get("skip_prefer_audio_rematch"))
            if params.get("prefer_audio") and not skip_rematch:
                source_url = str(params.get("source_url") or url)
                prefer_explicit = bool(params.get("allow_explicit", True))
                skip_rematch = (
                    se.is_youtube_music_watch_url(source_url)
                    or se.is_youtube_music_watch_url(url)
                )
                # Still rematch when the chosen upload is the wrong content
                # rating vs the user's explicit/clean preference.
                if skip_rematch:
                    from .metadata.parse import detect_content_rating

                    title_hint = str(params.get("source_title") or "")
                    rating = detect_content_rating(title_hint)
                    if (
                        (prefer_explicit and rating == "clean")
                        or (not prefer_explicit and rating == "explicit")
                    ):
                        skip_rematch = False
                if not skip_rematch:
                    orig = se.SearchResult(
                        url=params.get("source_url") or url,
                        title=params.get("source_title") or "",
                        uploader=params.get("source_uploader") or "",
                        duration_s=params.get("source_duration_s"),
                        view_count=None,
                        upload_date=None,
                        thumbnail_url=params.get("source_thumbnail_url"),
                    )
                    if not params.get("source_title"):
                        resolved = se.resolve_urls(
                            [url],
                            cookies_path=cookies,
                            cancel_event=job.cancel_event,
                            progress=log,
                        )
                        if resolved:
                            orig = resolved[0]
                    url = se.find_preferred_audio_url(
                        orig,
                        cookies_path=cookies,
                        cancel_event=job.cancel_event,
                        progress=log,
                        expected_artist=params.get("expected_artist"),
                        expected_title=params.get("expected_title"),
                        expected_duration_s=params.get("expected_duration_s"),
                        prefer_explicit=prefer_explicit,
                    )

            title_hint = str(params.get("source_title") or "").strip() or None
            uploader_hint = str(params.get("source_uploader") or "").strip() or None
            duration_hint = params.get("source_duration_s")
            if duration_hint is not None:
                try:
                    duration_hint = int(duration_hint)
                except (TypeError, ValueError):
                    duration_hint = None
            thumb_hint = params.get("source_thumbnail_url")
            if not isinstance(thumb_hint, str) or not thumb_hint.strip():
                thumb_hint = None

            result = dl.download_music(
                [url], params["output_dir"],
                cookies_path=cookies,
                verbose=verbose,
                progress=log,
                on_pct=on_progress,
                cancel_event=job.cancel_event,
                prefer_explicit=bool(params.get("allow_explicit", True)),
                title_hint=title_hint,
                uploader_hint=uploader_hint,
                duration_hint=duration_hint,
                thumbnail_hint=thumb_hint,
                # Start bytes ASAP; iTunes naming/tags happen in postprocess.
                defer_itunes=bool(title_hint),
            )
            if not result.success:
                job.state = FAILED
                job.error = result.message or "see log"
            elif result.output_paths:
                enrich = bool(params.get("enrich_metadata", True))
                lyrics = bool(params.get("download_lyrics", True))
                for i, path in enumerate(result.output_paths):
                    info = (
                        result.track_infos[i]
                        if i < len(result.track_infos)
                        else None
                    )
                    # Album downloads pass source_album — skip the downloader's
                    # per-song iTunes guess so postprocess can match in album context.
                    prefetch_match = (
                        None
                        if params.get("source_album")
                        else (info.itunes_match if info else None)
                    )
                    track_info = mp.TrackInfo(
                        title=info.title if info else "",
                        uploader=info.uploader if info else "",
                        parsed_artist=info.parsed_artist if info else "",
                        parsed_title=info.parsed_title if info else "",
                        duration_s=info.duration_s if info else None,
                        thumbnail_url=info.thumbnail_url if info else None,
                        itunes_match=prefetch_match,
                        source_album=params.get("source_album") or "",
                        source_album_artist=params.get("source_album_artist") or "",
                        source_track_number=params.get("source_track_number"),
                        source_disc_number=params.get("source_disc_number"),
                        source_cover_url=params.get("source_cover_url"),
                    )
                    pp = mp.process_track(
                        path,
                        track_info=track_info,
                        enrich_metadata=enrich,
                        download_lyrics=lyrics,
                        prefer_explicit=bool(params.get("allow_explicit", True)),
                        progress=log,
                        cancel_event=job.cancel_event,
                    )
                    if not pp.success:
                        job.state = FAILED
                        job.error = pp.message or "post-process failed"
                        break
                    if params.get("add_to_apple_music"):
                        imp = am.import_to_library(
                            pp.final_path,
                            progress=log,
                            cancel_event=job.cancel_event,
                            remove_source=bool(params.get("apple_music_only")),
                        )
                        if not imp.success:
                            log(
                                f"WARN: Apple Music import failed"
                                f" — {imp.message}",
                            )

        elif job.kind == "embed_single":
            result = em.embed_single(
                Path(params["video"]), Path(params["thumb"]),
                Path(params["output_dir"]),
                progress=log,
                cancel_event=job.cancel_event,
            )
            if result.failed and not result.processed:
                job.state = FAILED
                job.error = "embed failed"

        elif job.kind == "embed_folder":
            result = em.embed_folder(
                Path(params["video_dir"]), Path(params["thumb_dir"]),
                Path(params["output_dir"]),
                progress=log,
                cancel_event=job.cancel_event,
            )
            if result.failed and not result.processed:
                job.state = FAILED
                job.error = "embed failed"

        elif job.kind in ("search", "search_more"):
            # Search itself runs through the queue so the GUI sees one
            # consistent status panel. `search_more` is the same call with a
            # larger limit; the listener slices off the already-shown prefix.
            from . import search as se
            if (
                job.kind == "search_more"
                and params.get("results_context") == "music"
            ):
                already = int(params.get("already_loaded", 0))
                limit = int(params.get("limit", 20))
                track_count_requested = max(0, limit - already)
                tracks, albums, albums_exhausted = se.search_youtube_more_music(
                    params["query"],
                    track_offset=already,
                    track_count=track_count_requested,
                    album_offset=int(params.get("album_offset", 0)),
                    album_fetch_count=int(params.get("album_fetch_count", 0)),
                    cookies_path=cookies,
                    cancel_event=job.cancel_event,
                    progress=log,
                    verbose=verbose,
                    videos_only=bool(params.get("videos_only", True)),
                    audio_only=bool(params.get("audio_only", False)),
                    use_youtube_music=bool(params.get("use_youtube_music", False)),
                )
                tracks_exhausted = (
                    track_count_requested > 0 and len(tracks) < track_count_requested
                )
                job.result = {
                    "tracks": tracks,
                    "albums": albums,
                    "albums_exhausted": albums_exhausted,
                    "tracks_exhausted": tracks_exhausted,
                }
            else:
                if job.kind == "search_more":
                    already = int(params.get("already_loaded", 0))
                    limit = int(params.get("limit", 20))
                    count = max(0, limit - already)
                    new_items, _, tracks_exhausted = se._search_youtube_more_regular(
                        params["query"],
                        track_offset=already,
                        track_count=count,
                        cookies_path=cookies,
                        cancel_event=job.cancel_event,
                        progress=log,
                        verbose=verbose,
                        videos_only=bool(params.get("videos_only", True)),
                        audio_only=bool(params.get("audio_only", False)),
                    )
                    job.result = {
                        "tracks": new_items,
                        "tracks_exhausted": tracks_exhausted,
                    }
                else:
                    stream_music = (
                        params.get("results_context") == "music"
                        and bool(params.get("use_youtube_music"))
                    )

                    def on_partial(phase: str, items: list) -> None:
                        if job.cancel_event.is_set():
                            raise _Cancelled()
                        job.result = {
                            "partial": True,
                            "phase": phase,
                            "items": items,
                        }
                        self._notify(job)

                    job.result = se.search_youtube(
                        params["query"],
                        limit=params.get("limit", 20),
                        cookies_path=cookies,
                        cancel_event=job.cancel_event,
                        progress=log,
                        videos_only=bool(params.get("videos_only", True)),
                        audio_only=bool(params.get("audio_only", False)),
                        use_youtube_music=bool(params.get("use_youtube_music", False)),
                        include_albums=bool(params.get("include_albums", False)),
                        include_playlists=bool(params.get("include_playlists", False)),
                        album_limit=int(params.get("album_limit") or 5),
                        verbose=verbose,
                        on_partial=on_partial if stream_music else None,
                    )

        elif job.kind == "resolve":
            from . import search as se
            results = se.resolve_urls(
                params["urls"],
                cookies_path=cookies,
                cancel_event=job.cancel_event,
                progress=log,
                verbose=verbose,
            )
            job.result = results

        elif job.kind == "source_resolve":
            from .sources import resolve as resolve_source
            tracks = resolve_source(
                params["platform"],
                params.get("urls") or [],
                text=params.get("text") or "",
                progress=log,
                cancel_event=job.cancel_event,
                cookies_path=cookies,
            )
            job.result = tracks

        elif job.kind == "source_match_all":
            import time

            from . import search as se
            from .match_config import get_match_config
            from .sources.base import MATCH_PENDING, MusicTrack

            raw_tracks = params.get("tracks") or []
            tracks = [
                t if isinstance(t, MusicTrack) else MusicTrack.from_dict(t)
                for t in raw_tracks
            ]
            match_quality = str(params.get("match_quality") or "balanced")
            cfg = get_match_config(match_quality)
            total = len(tracks)
            matched: list[MusicTrack] = []
            stream_match = params.get("results_context") == "music"
            if stream_match:
                job.result = list(tracks)
                self._notify(job)
            match_started = time.monotonic()
            for i, track in enumerate(tracks):
                if job.cancel_event.is_set():
                    raise _Cancelled()
                if track.match_status != MATCH_PENDING:
                    matched.append(track)
                    continue
                label = track.display_title(50)
                log(f"Matching {i + 1}/{total} ({cfg.name}): {label}")
                result = se.find_youtube_match_for_track(
                    track.artist,
                    track.title,
                    track.duration_s,
                    cookies_path=cookies,
                    cancel_event=job.cancel_event,
                    progress=log,
                    use_youtube_music=bool(params.get("use_youtube_music", False)),
                    audio_only=bool(params.get("audio_only", True)),
                    match_quality=match_quality,
                    prefer_explicit=bool(params.get("allow_explicit", True)),
                )
                matched.append(track.with_match(result))
                if stream_match:
                    job.result = matched + tracks[i + 1:]
                    self._notify(job)
                pending_left = total - (i + 1)
                if pending_left > 0 and cfg.inter_track_delay_s > 0:
                    time.sleep(cfg.inter_track_delay_s)
            elapsed = int(time.monotonic() - match_started)
            ok = sum(1 for t in matched if t.is_downloadable())
            log(f"Matched {ok}/{total} in {elapsed // 60}m {elapsed % 60}s ({cfg.name})")
            job.result = matched

        else:
            raise ValueError(f"Unknown job kind: {job.kind}")


# ----------------------- cancellation sentinel ---------------------------- #

class _Cancelled(Exception):
    """Raised inside a worker callback to abort the running job."""


def _clamp_pct(pct: float) -> float:
    try:
        v = float(pct)
    except (TypeError, ValueError):
        return 0.0
    if v != v:  # NaN
        return 0.0
    if v < 0.0:
        return 0.0
    if v > 100.0:
        return 100.0
    return v
