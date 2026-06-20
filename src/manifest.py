from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .uploader import R2Uploader

logger = logging.getLogger(__name__)

MAX_MANIFEST_EVENTS = 500


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
