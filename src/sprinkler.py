from __future__ import annotations

import logging
import time

from gpiozero import OutputDevice


logger = logging.getLogger(__name__)


class Sprinkler:
    def __init__(self, pin: int, active_high: bool = True) -> None:
        self._device = OutputDevice(
            pin,
            active_high=active_high,
            initial_value=False,
        )
        self._pin = pin

    def fire(self, duration_s: float) -> None:
        logger.info("sprinkler_on", extra={"pin": self._pin, "duration_s": duration_s})
        self._device.on()
        try:
            time.sleep(duration_s)
        finally:
            self._device.off()
            logger.info("sprinkler_off", extra={"pin": self._pin})

    def close(self) -> None:
        try:
            self._device.off()
        finally:
            self._device.close()

    def __enter__(self) -> "Sprinkler":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
