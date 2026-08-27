"""Persistent key/value settings store.

Replaces the Java app's data.json. Stored in the user's OS-appropriate config
directory so it travels across reinstalls and doesn't pollute the project tree:

    macOS:    ~/Library/Application Support/easy-dlp/settings.json
    Linux:    $XDG_CONFIG_HOME/easy-dlp/settings.json (default ~/.config/...)
    Windows:  %APPDATA%/easy-dlp/settings.json

Old-key migration: previous versions of the app split URLs into audio_url,
video_url, and thumb_url. The new UI has one combined paste textbox so we
fold those into `paste_urls` on load and drop the originals on next save.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from threading import Lock
from typing import Any


_APP_NAME = "easy-dlp"
_LEGACY_APP_NAME = "ytdlp-app"

_DEFAULTS: dict[str, Any] = {
    # Output folders
    "audio_dir": str(Path.home() / "Music"),
    "video_dir": str(Path.home() / "Movies"),
    "thumb_dir": str(Path.home() / "Pictures"),

    # Embed-tab paths
    "embed_video_dir": "",
    "embed_thumb_dir": "",
    "embed_out_dir": "",
    "embed_mode": "folder",          # "folder" or "single"

    # Optional cookies file
    "cookies_path": "",
    "verbose": False,

    # Music tab
    "music_dir": str(Path.home() / "Music"),
    "music_search_query": "",
    "music_paste_urls": "",
    "music_paste_platform": "youtube",
    "music_track_list": "",
    "music_source_tab": "search",    # "search" or "paste"
    "music_search_limit": 20,        # 10 | 20 | 50
    "music_download_lyrics": True,
    "music_prefer_audio": True,
    "music_allow_explicit": True,
    "music_search_audio_only": True,
    "music_use_youtube_music": True,
    "music_include_albums": True,
    "music_include_playlists": False,
    "music_album_search_limit": 5,
    "music_skip_duplicates": False,
    "music_match_quality": "balanced",  # "fast" | "balanced" | "accurate"
    "music_add_to_apple_music": False,
    "music_apple_music_only": False,

    # Combined download tab state
    "paste_urls": "",
    "search_query": "",
    "search_limit": 20,              # 10 | 20 | 50
    "source_tab": "search",          # "search" or "paste"
    # Channels/playlists are always filtered out (not a setting). Only
    # the audio-only heuristic is user-togglable.
    "search_audio_only": False,

    # UI collapse state
    "panel_active_collapsed": True,  # idle: activity dock collapsed
    "panel_recent_collapsed": True,
    "panel_log_collapsed": True,
    "panel_log_height": "normal",    # "normal" | "large" | "xlarge"
    "panel_active_height": "normal", # "normal" | "large" | "xlarge"
    "panel_recent_height": "normal", # "normal" | "large" | "xlarge"
    "activity_dock_segment": "active",  # "active" | "recent" | "log"
    "download_input_collapsed": False,
    "music_input_collapsed": False,
    "download_options_collapsed": True,
    "music_options_collapsed": True,
    "music_track_list_expanded": False,
    "main_tab": "music",             # "music" | "download" | "settings"

    # Main window size (restored on launch)
    "window_width": 1280,
    "window_height": 1000,

    # Default format selection on launch
    "default_audio": True,
    "default_video": False,
    "default_thumb": False,

    # Concurrency
    "max_parallel_downloads": 2,

    # Appearance
    "theme": "system",               # "system" | "light" | "dark"

    # Scroll direction for the results / active / recent panels.
    #   "auto"     – follow macOS' "Natural scrolling" preference
    #   "natural"  – content follows your fingers / wheel-up = page down
    #   "inverted" – content moves opposite to fingers / wheel-up = page up
    "scroll_direction": "auto",
}

# Old keys to migrate away from on first load.
_LEGACY_KEYS = ("audio_url", "video_url", "thumb_url")


def _config_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / _APP_NAME
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / _APP_NAME
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / _APP_NAME


def _legacy_config_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / _LEGACY_APP_NAME
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / _LEGACY_APP_NAME
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / _LEGACY_APP_NAME


class Settings:
    """Thread-safe JSON-backed settings store.

    The in-memory dict is guarded by a lock, but disk writes happen outside
    the lock to avoid blocking other threads on I/O.
    """

    def __init__(self, path: Path | None = None) -> None:
        new_path = _config_dir() / "settings.json"
        if path is None:
            legacy_path = _legacy_config_dir() / "settings.json"
            load_path = (
                legacy_path
                if not new_path.exists() and legacy_path.exists()
                else new_path
            )
            save_path = new_path
        else:
            load_path = path
            save_path = path
        self._path = load_path
        self._save_path = save_path
        self._lock = Lock()
        self._data: dict[str, Any] = copy.deepcopy(_DEFAULTS)
        self._load()
        if self._path != self._save_path:
            self._path = self._save_path
            self._flush(json.dumps(self._data, indent=2))

    @property
    def path(self) -> Path:
        return self._path

    # --- IO -------------------------------------------------------------- #

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with self._path.open("r", encoding="utf-8") as f:
                stored = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(stored, dict):
            return

        with self._lock:
            for k, v in stored.items():
                if k in _DEFAULTS:
                    self._data[k] = v
            # Migrate legacy URL keys into `paste_urls`.
            if not self._data.get("paste_urls"):
                legacy_blobs = [
                    str(stored[k]).strip()
                    for k in _LEGACY_KEYS
                    if isinstance(stored.get(k), str) and stored[k].strip()
                ]
                if legacy_blobs:
                    self._data["paste_urls"] = "\n".join(legacy_blobs)
            # Search inputs are always visible in the current UI.
            self._data["download_input_collapsed"] = False
            self._data["music_input_collapsed"] = False
            snapshot = json.dumps(self._data, indent=2)
        # If anything changed during the load (migration), persist it.
        self._flush(snapshot)

    def _flush(self, snapshot: str) -> None:
        try:
            target = self._save_path
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".json.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                f.write(snapshot)
            tmp.replace(target)
        except OSError:
            pass  # disk full / read-only — best-effort

    # --- API ------------------------------------------------------------- #

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if key in self._data:
                return self._data[key]
        if default is not None:
            return default
        return _DEFAULTS.get(key, "")

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            snapshot = json.dumps(self._data, indent=2)
        self._flush(snapshot)

    def update(self, **kwargs: Any) -> None:
        with self._lock:
            self._data.update(kwargs)
            snapshot = json.dumps(self._data, indent=2)
        self._flush(snapshot)

    def reset_to_defaults(self) -> None:
        with self._lock:
            self._data = copy.deepcopy(_DEFAULTS)
            snapshot = json.dumps(self._data, indent=2)
        self._flush(snapshot)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)
