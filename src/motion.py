from __future__ import annotations

import io
import logging

from PIL import Image, ImageChops, ImageStat


logger = logging.getLogger(__name__)


class MotionDetector:
    def __init__(self, threshold: float = 5.0, downscale: int = 320) -> None:
        self._threshold = threshold
        self._downscale = downscale
        self._prev: Image.Image | None = None

    def _prepare(self, image_bytes: bytes) -> Image.Image:
        img = Image.open(io.BytesIO(image_bytes))
        img.thumbnail((self._downscale, self._downscale))
        return img.convert("L")

    def check(self, image_bytes: bytes) -> tuple[bool, float]:
        current = self._prepare(image_bytes)
        if self._prev is None:
            self._prev = current
            return True, float("inf")

        if self._prev.size != current.size:
            self._prev = current
            return True, float("inf")

        diff = ImageChops.difference(self._prev, current)
        mean_diff = ImageStat.Stat(diff).mean[0]
        self._prev = current
        return mean_diff >= self._threshold, mean_diff
