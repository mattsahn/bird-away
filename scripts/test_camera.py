"""Grab a single frame from the configured RTSP camera and save it.

Usage: python scripts/test_camera.py [output_path]
Defaults output to /tmp/bird-away-test.jpg.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import camera
from src.config import load_config


def main() -> int:
    cfg = load_config()
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/bird-away-test.jpg")
    frame = camera.capture_frame(cfg.rtsp_url)
    out.write_bytes(frame)
    print(f"wrote {len(frame)} bytes to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
