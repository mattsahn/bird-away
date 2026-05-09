"""Run each status LED pattern in sequence so you can confirm wiring.

Usage: python scripts/test_status_led.py

Sequence:
  1. Heartbeat blink (0.5s on)
  2. Photo blink (1.5s on)
  3. Bird-detected pattern (5 rapid blinks)
  4. One more heartbeat
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import load_config
from src.status_led import (
    BIRD_BLINK_COUNT,
    BIRD_BLINK_OFF_S,
    BIRD_BLINK_ON_S,
    HEARTBEAT_BLINK_S,
    PHOTO_BLINK_S,
    StatusLed,
)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config()
    if not cfg.status_led_enabled:
        print("status_led_enabled is false in config.yaml — set it to true to test")
        return 1

    print(f"driving status LED on GPIO {cfg.status_led_pin}")
    with StatusLed(cfg.status_led_pin, enabled=True) as led:
        # The thread fires its own heartbeat every 10s. Force the patterns
        # immediately by enqueueing them so the test doesn't take 30+ seconds.
        bird_total = (
            BIRD_BLINK_COUNT * BIRD_BLINK_ON_S
            + (BIRD_BLINK_COUNT - 1) * BIRD_BLINK_OFF_S
        )

        print("  → heartbeat (waiting up to 10s for first tick)")
        time.sleep(HEARTBEAT_BLINK_S + 10.5)

        print("  → photo blink (1.5s)")
        led.photo()
        time.sleep(PHOTO_BLINK_S + 0.5)

        print(f"  → bird-detected pattern ({BIRD_BLINK_COUNT} rapid blinks)")
        led.bird_detected()
        time.sleep(bird_total + 0.5)

        print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
