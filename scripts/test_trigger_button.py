"""Confirm the manual-trigger switch is wired correctly.

Usage: python scripts/test_trigger_button.py

While running, press and release the button. Each press should turn the
status LED on; each release should turn it off and print a message.
Ctrl-C to exit.
"""
from __future__ import annotations

import logging
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import load_config
from src.status_led import StatusLed
from src.trigger_button import TriggerButton


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config()
    if not cfg.trigger_button_enabled:
        print("trigger_button_enabled is false in config.yaml — set it to true to test")
        return 1

    print(
        f"button on GPIO {cfg.trigger_button_pin}, "
        f"LED on GPIO {cfg.status_led_pin}. press and release to test. Ctrl-C to exit."
    )

    with StatusLed(cfg.status_led_pin, enabled=cfg.status_led_enabled) as led:

        def on_press() -> None:
            print("PRESS")
            led.hold()

        def on_release() -> None:
            print("RELEASE")
            led.release()
            led.bird_detected()

        with TriggerButton(cfg.trigger_button_pin, on_press, on_release):
            signal.pause()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
