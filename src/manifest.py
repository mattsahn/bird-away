from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .uploader import R2Uploader

logger = logging.getLogger(__name__)

MAX_MANIFEST_EVENTS = 500

# Safety cap on real-time entries kept in the manifest, independent of the
# time-window prune (guards against a misconfigured huge window).
MAX_REALTIME_FRAMES = 600

TS_FORMAT = "%Y%m%dT%H%M%SZ"


class ManifestManager:
    """Maintains a ``manifest.json`` in R2 that the web dashboard reads."""

    def __init__(
        self,
        uploader: R2Uploader,
        key_prefix: str,
        public_base_url: str,
    ) -> None:
        self._uploader = uploader
        self._key = f"{key_prefix}/manifest.json"
        self._public_base_url = public_base_url.rstrip("/")
        self._events: list[dict[str, str]] = []
        self._load()

    def _load(self) -> None:
        try:
            data = self._uploader.download_json(self._key)
            if data and "events" in data:
                self._events = data["events"]
                logger.info("manifest_loaded events=%d", len(self._events))
        except Exception:
            logger.info("manifest_not_found starting_fresh")
            self._events = []

    def add_event(
        self,
        ts: str,
        date: str,
        image_key: str,
        video_key: str,
        trigger: str = "auto",
    ) -> None:
        event: dict[str, str] = {
            "ts": ts,
            "date": date,
            "image_url": f"{self._public_base_url}/{image_key}",
            "video_url": f"{self._public_base_url}/{video_key}",
            "trigger": trigger,
        }
        self._events.insert(0, event)
        if len(self._events) > MAX_MANIFEST_EVENTS:
            self._events = self._events[:MAX_MANIFEST_EVENTS]
        self._save()

    def _save(self) -> None:
        manifest = {
            "device": "bird-away",
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "base_url": self._public_base_url,
            "total_events": len(self._events),
            "events": self._events,
        }
        payload = json.dumps(manifest, indent=2).encode("utf-8")
        try:
            self._uploader.upload_bytes(
                payload, self._key, content_type="application/json",
            )
            logger.info("manifest_updated events=%d", len(self._events))
        except Exception:
            logger.exception("manifest_upload_failed")


class RealtimeManifest:
    """Maintains a rolling ``manifest.json`` of recent periodic frames.

    Unlike :class:`ManifestManager` (durable, real detections only), this index
    holds every frame the loop captured within the last ``window_minutes`` so
    the dashboard's Live tab can show what the detector actually saw. Entries
    older than the window are pruned on each add; the matching R2 objects are
    expired separately by a bucket lifecycle rule.
    """

    def __init__(
        self,
        uploader: R2Uploader,
        key_prefix: str,
        public_base_url: str,
        window_minutes: int,
    ) -> None:
        self._uploader = uploader
        self._key = f"{key_prefix}/manifest.json"
        self._public_base_url = public_base_url.rstrip("/")
        self._window_minutes = window_minutes
        self._frames: list[dict[str, object]] = []
        self._load()

    def _load(self) -> None:
        try:
            data = self._uploader.download_json(self._key)
            if data and "frames" in data:
                self._frames = data["frames"]
                logger.info("realtime_manifest_loaded frames=%d", len(self._frames))
        except Exception:
            logger.info("realtime_manifest_not_found starting_fresh")
            self._frames = []

    def _prune(self, now: datetime) -> None:
        cutoff = now - timedelta(minutes=self._window_minutes)
        kept: list[dict[str, object]] = []
        for f in self._frames:
            try:
                ft = datetime.strptime(str(f["ts"]), TS_FORMAT).replace(
                    tzinfo=timezone.utc
                )
            except (ValueError, KeyError):
                continue
            if ft >= cutoff:
                kept.append(f)
        self._frames = kept[:MAX_REALTIME_FRAMES]

    def add_frame(
        self,
        ts: str,
        date: str,
        image_key: str,
        detected: bool,
        status: str,
    ) -> None:
        frame: dict[str, object] = {
            "ts": ts,
            "date": date,
            "image_url": f"{self._public_base_url}/{image_key}",
            "detected": detected,
            "status": status,
        }
        self._frames.insert(0, frame)
        self._prune(datetime.now(timezone.utc))
        self._save()

    def _save(self) -> None:
        manifest = {
            "device": "bird-away",
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "base_url": self._public_base_url,
            "window_minutes": self._window_minutes,
            "total_frames": len(self._frames),
            "frames": self._frames,
        }
        payload = json.dumps(manifest, indent=2).encode("utf-8")
        try:
            self._uploader.upload_bytes(
                payload, self._key, content_type="application/json",
            )
            logger.info("realtime_manifest_updated frames=%d", len(self._frames))
        except Exception:
            logger.exception("realtime_manifest_upload_failed")
