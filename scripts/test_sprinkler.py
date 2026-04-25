"""Fire the relay for a short duration to confirm wiring.

Usage: python scripts/test_sprinkler.py [duration_seconds]
Default duration is 1 second to avoid soaking testers.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import load_config
from src.sprinkler import Sprinkler


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
    cfg = load_config()
    with Sprinkler(cfg.gpio_pin, active_high=cfg.relay_active_high) as sprinkler:
        sprinkler.fire(duration)
    return 0


if __name__ == "__main__":
    sys.exit(main())
