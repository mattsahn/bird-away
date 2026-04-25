from __future__ import annotations

import logging
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

from . import camera
from .config import Config, load_config
from .detector import Detector
from .motion import MotionDetector
from .sprinkler import Sprinkler


logger = logging.getLogger("bird_away")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )


class _Stop:
    def __init__(self) -> None:
        self.requested = False

    def request(self, *_args) -> None:
        if not self.requested:
            logger.info("shutdown_requested")
        self.requested = True


def _handle_event(cfg: Config, frame: bytes, sprinkler: Sprinkler) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    image_path = cfg.capture_dir / f"detection-{ts}.jpg"
    video_path = cfg.capture_dir / f"event-{ts}.mp4"
    image_path.write_bytes(frame)

    rec = camera.start_recording(cfg.rtsp_url, video_path, cfg.video_duration)
    try:
        sprinkler.fire(cfg.spray_duration)
    finally:
        try:
            rec.wait(timeout=cfg.video_duration + 10)
        except subprocess.TimeoutExpired:
            logger.warning("video_recording_timeout, killing ffmpeg")
            rec.kill()
            rec.wait()
    logger.info(
        "bird_detected_and_sprayed",
        extra={"ts": ts, "image": str(image_path), "video": str(video_path)},
    )


def main() -> int:
    cfg = load_config()
    _setup_logging(cfg.log_level)
    cfg.capture_dir.mkdir(parents=True, exist_ok=True)
    logger.info("starting", extra={"capture_dir": str(cfg.capture_dir)})

    stop = _Stop()
    signal.signal(signal.SIGTERM, stop.request)
    signal.signal(signal.SIGINT, stop.request)

    detector = Detector(
        cfg.openrouter_api_key,
        model=cfg.detector_model,
        base_url=cfg.detector_base_url,
    )
    motion = (
        MotionDetector(threshold=cfg.motion_threshold, downscale=cfg.motion_downscale)
        if cfg.motion_enabled
        else None
    )

    with Sprinkler(cfg.gpio_pin, active_high=cfg.relay_active_high) as sprinkler:
        while not stop.requested:
            iter_start = time.monotonic()
            try:
                frame = camera.capture_frame(cfg.rtsp_url)
                run_detector = True
                if motion is not None:
                    moved, score = motion.check(frame)
                    run_detector = moved
                    logger.debug(
                        "motion" if moved else "no_motion", extra={"score": score}
                    )
                if run_detector:
                    if detector.is_bird_present(frame):
                        _handle_event(cfg, frame, sprinkler)
                    else:
                        logger.debug("no_bird")
            except Exception:
                logger.exception("loop_iteration_failed")

            elapsed = time.monotonic() - iter_start
            sleep_for = max(0.0, cfg.interval_seconds - elapsed)
            end = time.monotonic() + sleep_for
            while not stop.requested and time.monotonic() < end:
                time.sleep(min(1.0, end - time.monotonic()))

    logger.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
