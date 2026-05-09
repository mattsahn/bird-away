from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .camera import Camera
from .config import Config, load_config
from .detector import Detector
from .motion import MotionDetector
from .sprinkler import Sprinkler
from .status_led import StatusLed
from .trigger_button import TriggerButton
from .uploader import R2Uploader, make_uploader


logger = logging.getLogger("bird_away")

DAYTIME_START_HOUR = 7
DAYTIME_END_HOUR = 19

RETENTION_SWEEP_INTERVAL_S = 3600.0
CAPTURE_GLOBS = ("detection-*.jpg", "event-*.mp4")


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


def _sd_notify(message: str) -> None:
    """Send a state message to systemd via $NOTIFY_SOCKET. No-op if unset."""
    sock_path = os.environ.get("NOTIFY_SOCKET")
    if not sock_path:
        return
    # Abstract namespace sockets are prefixed with "@" in the env var.
    if sock_path.startswith("@"):
        sock_path = "\0" + sock_path[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.sendto(message.encode("utf-8"), sock_path)
    except OSError as e:
        logger.warning("sd_notify_failed %s message=%s", type(e).__name__, message)


def _ping_healthcheck(url: str, timeout: float = 5.0) -> None:
    if not url:
        return
    try:
        httpx.get(url, timeout=timeout)
    except Exception as e:
        logger.warning("healthcheck_ping_failed %s", type(e).__name__)


def _prune_old_captures(capture_dir: Path, retention_days: int) -> None:
    if retention_days <= 0:
        return
    cutoff = time.time() - retention_days * 86400
    removed = 0
    freed = 0
    for pattern in CAPTURE_GLOBS:
        for path in capture_dir.glob(pattern):
            try:
                st = path.stat()
                if st.st_mtime < cutoff:
                    size = st.st_size
                    path.unlink()
                    removed += 1
                    freed += size
            except FileNotFoundError:
                pass
            except Exception:
                logger.exception("retention_unlink_failed path=%s", path)
    if removed:
        logger.info(
            "retention_swept removed=%d freed_bytes=%d retention_days=%d",
            removed, freed, retention_days,
        )


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
    _sd_notify("READY=1")
    _prune_old_captures(cfg.capture_dir, cfg.retention_days)
    next_prune_at = time.monotonic() + RETENTION_SWEEP_INTERVAL_S
    if cfg.healthcheck_url:
        logger.info(
            "healthcheck_enabled interval=%ds", cfg.healthcheck_interval_seconds,
        )
    last_healthcheck_at = 0.0

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

    handler_lock = threading.Lock()

    with Camera(cfg.rtsp_url) as cam, Sprinkler(
        cfg.gpio_pin, active_high=cfg.relay_active_high
    ) as sprinkler, StatusLed(
        cfg.status_led_pin, enabled=cfg.status_led_enabled
    ) as status_led:

        def on_button_press() -> None:
            logger.info("manual_trigger_button_pressed")
            status_led.hold()

        def on_button_release() -> None:
            logger.info("manual_trigger_button_released")
            status_led.release()
            if not handler_lock.acquire(blocking=False):
                logger.info("manual_trigger_skipped event_already_running")
                return
            try:
                status_led.bird_detected()
                try:
                    frame = cam.capture_frame()
                    prepared = detector.prepare_image(frame)
                    _handle_event(cfg, cam, prepared, sprinkler, uploader=uploader)
                except Exception:
                    logger.exception("manual_trigger_failed")
            finally:
                handler_lock.release()

        trigger = (
            TriggerButton(cfg.trigger_button_pin, on_button_press, on_button_release)
            if cfg.trigger_button_enabled
            else None
        )
        try:
            while not stop.requested:
                _sd_notify("WATCHDOG=1")
                iter_start = time.monotonic()
                if iter_start >= next_prune_at:
                    _prune_old_captures(cfg.capture_dir, cfg.retention_days)
                    next_prune_at = time.monotonic() + RETENTION_SWEEP_INTERVAL_S
                iteration_ok = False
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
                    status_led.photo()
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
                        prepared = detector.prepare_image(frame)
                        if detector.is_bird_present(prepared):
                            status_led.bird_detected()
                            with handler_lock:
                                _handle_event(cfg, cam, prepared, sprinkler, uploader=uploader)
                        else:
                            logger.info("detector_result=no_bird")
                    iteration_ok = True
                except _SkipIteration:
                    iteration_ok = True
                except Exception:
                    logger.exception("loop_iteration_failed")

                if (
                    iteration_ok
                    and cfg.healthcheck_url
                    and time.monotonic() - last_healthcheck_at
                    >= cfg.healthcheck_interval_seconds
                ):
                    _ping_healthcheck(cfg.healthcheck_url)
                    last_healthcheck_at = time.monotonic()

                elapsed = time.monotonic() - iter_start
                sleep_for = max(0.0, cfg.interval_seconds - elapsed)
                end = time.monotonic() + sleep_for
                while not stop.requested and time.monotonic() < end:
                    _sd_notify("WATCHDOG=1")
                    time.sleep(min(1.0, end - time.monotonic()))
        finally:
            _sd_notify("STOPPING=1")
            if trigger is not None:
                trigger.close()

    logger.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
