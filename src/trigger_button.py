from __future__ import annotations

import logging
from typing import Callable

from gpiozero import Button


logger = logging.getLogger(__name__)


class TriggerButton:
    """Wrapper around gpiozero.Button for the manual sprinkler trigger.

    Wiring: switch contacts between the GPIO pin and ground. The Pi's internal
    pull-up holds the input HIGH at rest; pressing pulls it LOW.

    Both callbacks fire on gpiozero's internal event thread, so they should
    return promptly. The on_release handler is the one that should kick off
    real work (capture / spray / record) — typically by enqueueing onto a
    serialized handler so that callback returns fast.
    """

    def __init__(
        self,
        pin: int,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        bounce_time: float = 0.05,
    ) -> None:
        self._pin = pin
        self._on_press = on_press
        self._on_release = on_release
        self._button = Button(pin, pull_up=True, bounce_time=bounce_time)
        self._button.when_pressed = self._handle_press
        self._button.when_released = self._handle_release

    def _handle_press(self) -> None:
        try:
            self._on_press()
        except Exception:
            logger.exception("trigger_button_on_press_failed pin=%d", self._pin)

    def _handle_release(self) -> None:
        try:
            self._on_release()
        except Exception:
            logger.exception("trigger_button_on_release_failed pin=%d", self._pin)

    def close(self) -> None:
        self._button.close()

    def __enter__(self) -> "TriggerButton":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
