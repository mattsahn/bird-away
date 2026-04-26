from __future__ import annotations

import logging
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .camera import Camera
from .config import Config, load_config
from .detector import Detector
from .motion import MotionDetector
from .sprinkler import Sprinkler
from .uploader import R2Uploader, make_uploader


logger = logging.getLogger("bird_away")

DAYTIME_START_HOUR = 7
DAYTIME_END_HOUR = 19


class _SkipIteration(Exception):
    pass


def _in_daytime() -> bool:
    return DAYTIME_START_HOUR <= datetime.now().hour < DAYTIME_END_HOUR


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


def _r2_key(cfg: Config, date_prefix: str, path: Path) -> str:
    return f"{cfg.r2_key_prefix}/{date_prefix}/{path.name}"


def _safe_upload(uploader: R2Uploader, local_path: Path, key: str) -> None:
    try:
        uploader.upload_file(local_path, key)
    except Exception:
        logger.exception("r2_upload_failed key=%s", key)


def _handle_event(
    cfg: Config,
    cam: Camera,
    frame: bytes,
    sprinkler: Sprinkler,
    uploader: R2Uploader | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%dT%H%M%SZ")
    date_prefix = now.strftime("%Y-%m-%d")
    image_path = cfg.capture_dir / f"detection-{ts}.jpg"
    video_path = cfg.capture_dir / f"event-{ts}.mp4"
    image_path.write_bytes(frame)

    if uploader is not None:
        _safe_upload(uploader, image_path, _r2_key(cfg, date_prefix, image_path))

    rec = cam.start_recording(video_path, cfg.video_duration)
    try:
        try:
            sprinkler.fire(cfg.spray_duration)
        finally:
            try:
                rec.wait(timeout=cfg.video_duration + 10)
            except subprocess.TimeoutExpired:
                logger.warning("video_recording_timeout, killing ffmpeg")
                rec.kill()
                rec.wait()
    finally:
        cam.resume()

    if uploader is not None and video_path.exists():
        _safe_upload(uploader, video_path, _r2_key(cfg, date_prefix, video_path))

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
        system_prompt=cfg.detector_prompt,
        model=cfg.detector_model,
        base_url=cfg.detector_base_url,
    )
    motion = (
        MotionDetector(threshold=cfg.motion_threshold, downscale=cfg.motion_downscale)
        if cfg.motion_enabled
        else None
    )
    uploader = make_uploader(cfg)
    if uploader is not None:
        logger.info("r2_uploader_enabled bucket=%s prefix=%s", cfg.r2_bucket, cfg.r2_key_prefix)

    with Camera(cfg.rtsp_url) as cam, Sprinkler(
        cfg.gpio_pin, active_high=cfg.relay_active_high
    ) as sprinkler:
        while not stop.requested:
            iter_start = time.monotonic()
            try:
                if cfg.daytime_only and not _in_daytime():
                    logger.info(
                        "skipping_outside_daytime hour=%d window=[%d,%d)",
                        datetime.now().hour,
                        DAYTIME_START_HOUR,
                        DAYTIME_END_HOUR,
                    )
                    raise _SkipIteration
                frame = cam.capture_frame()
                run_detector = True
                if motion is not None:
                    moved, score = motion.check(frame)
                    run_detector = moved
                    logger.info(
                        "motion score=%.2f threshold=%.2f %s",
                        score,
                        cfg.motion_threshold,
                        "above_threshold" if moved else "below_threshold_skip_detector",
                    )
                if run_detector:
                    if detector.is_bird_present(frame):
                        _handle_event(cfg, cam, frame, sprinkler, uploader=uploader)
                    else:
                        logger.info("detector_result=no_bird")
            except _SkipIteration:
                pass
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
