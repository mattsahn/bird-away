"""Send a local image to Claude and print the yes/no answer.

Usage: python scripts/test_detector.py path/to/image.jpg
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import load_config
from src.detector import Detector


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: test_detector.py <image.jpg>", file=sys.stderr)
        return 2
    image_path = Path(sys.argv[1])
    cfg = load_config()
    detector = Detector(
        cfg.openrouter_api_key,
        system_prompt=cfg.detector_prompt,
        model=cfg.detector_model,
        base_url=cfg.detector_base_url,
    )
    result = detector.is_bird_present(image_path.read_bytes())
    print("yes" if result else "no")
    return 0


if __name__ == "__main__":
    sys.exit(main())
