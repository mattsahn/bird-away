from __future__ import annotations

import logging
import threading
import time
from queue import Empty, Queue

from gpiozero import LED


logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_S = 10.0
HEARTBEAT_BLINK_S = 0.5
PHOTO_BLINK_S = 2.0
BIRD_BLINK_COUNT = 7
BIRD_BLINK_ON_S = 0.1
BIRD_BLINK_OFF_S = 0.1


class StatusLed:
    """Background-thread driver for a status LED.

    Heartbeat blinks every HEARTBEAT_INTERVAL_S to indicate the service is
    alive. photo() and bird_detected() enqueue one-shot patterns that preempt
    the next heartbeat and reset the schedule.
    """

    def __init__(self, pin: int, enabled: bool = True) -> None:
        self._enabled = enabled
        self._pin = pin
        self._held = threading.Event()
        if not enabled:
            self._led = None
            self._thread = None
            self._stop = None
            self._queue: Queue[str] = Queue()
            return
        self._led = LED(pin)
        self._stop = threading.Event()
        self._queue = Queue()
        self._thread = threading.Thread(
            target=self._run, name="status-led", daemon=True
        )
        self._thread.start()

    def photo(self) -> None:
        if self._enabled:
            self._queue.put("photo")

    def bird_detected(self) -> None:
        if self._enabled:
            self._queue.put("bird")

    def hold(self) -> None:
        """Pin the LED on. Suppresses heartbeat / one-shot patterns until released."""
        if not self._enabled:
            return
        self._held.set()
        if self._led is not None:
            self._led.on()

    def release(self) -> None:
        """Release a hold. LED returns off and the heartbeat resumes."""
        if not self._enabled:
            return
        self._held.clear()
        if self._led is not None:
            self._led.off()

    def _run(self) -> None:
        next_heartbeat = time.monotonic() + HEARTBEAT_INTERVAL_S
        while not self._stop.is_set():
            timeout = max(0.0, next_heartbeat - time.monotonic())
            try:
                event = self._queue.get(timeout=timeout)
            except Empty:
                event = None

            if self._stop.is_set():
                break

            if self._held.is_set():
                # The button owns the LED right now — skip this tick.
                next_heartbeat = time.monotonic() + HEARTBEAT_INTERVAL_S
                continue

            try:
                if event == "photo":
                    self._solid(PHOTO_BLINK_S)
                elif event == "bird":
                    self._rapid(BIRD_BLINK_COUNT, BIRD_BLINK_ON_S, BIRD_BLINK_OFF_S)
                else:
                    self._solid(HEARTBEAT_BLINK_S)
            except Exception:
                logger.exception("status_led_pattern_failed event=%s", event)

            next_heartbeat = time.monotonic() + HEARTBEAT_INTERVAL_S

    def _solid(self, duration: float) -> None:
        if self._held.is_set():
            return
        self._led.on()
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            if self._held.is_set():
                return
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        self._led.off()

    def _rapid(self, count: int, on_s: float, off_s: float) -> None:
        for i in range(count):
            if self._held.is_set():
                return
            self._led.on()
            time.sleep(on_s)
            if self._held.is_set():
                return
            self._led.off()
            if i < count - 1:
                time.sleep(off_s)

    def close(self) -> None:
        if not self._enabled:
            return
        self._stop.set()
        # Nudge the thread out of queue.get() in case timeout is still long.
        self._queue.put("__stop__")
        if self._thread is not None:
            self._thread.join(timeout=2)
        try:
            if self._led is not None:
                self._led.off()
        finally:
            if self._led is not None:
                self._led.close()

    def __enter__(self) -> "StatusLed":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
