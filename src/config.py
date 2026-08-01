from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import time as dtime
from pathlib import Path

import yaml
from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    openrouter_api_key: str
    rtsp_url: str
    interval_seconds: int
    spray_duration: float
    pre_spray_seconds: float
    post_spray_seconds: float
    gpio_pin: int
    relay_active_high: bool
    capture_dir: Path
    detector_model: str
    detector_base_url: str
    detector_prompt: str
    detector_max_image_dim: int
    motion_enabled: bool
    motion_threshold: float
    motion_downscale: int
    log_level: str
    daytime_only: bool
    daytime_start: dtime
    daytime_end: dtime
    r2_enabled: bool
    r2_account_id: str
    r2_bucket: str
    r2_public_base_url: str
    r2_key_prefix: str
    status_led_enabled: bool
    status_led_pin: int
    trigger_button_enabled: bool
    trigger_button_pin: int
    retention_days: int
    healthcheck_url: str
    healthcheck_interval_seconds: int
    delete_after_upload: bool
    realtime_enabled: bool
    realtime_key_prefix: str
    realtime_window_minutes: int
    realtime_max_image_dim: int


DEFAULTS = {
    "interval_seconds": 60,
    "spray_duration": 3,
    "pre_spray_seconds": 3,
    "post_spray_seconds": 4,
    "gpio_pin": 17,
    "relay_active_high": True,
    "capture_dir": "./captures",
    "detector_model": "google/gemini-3-flash-preview",
    "detector_base_url": "https://openrouter.ai/api/v1",
    "detector_prompt": (
        "You are a bird detector for a backyard pool. "
        "Respond with exactly 'yes' if you see one or more birds in, on, or "
        "near the pool (including birds in flight directly above it). "
        "Respond with exactly 'no' otherwise. Output only the single word."
    ),
    "detector_max_image_dim": 0,
    "motion_enabled": True,
    "motion_threshold": 5.0,
    "motion_downscale": 320,
    "log_level": "INFO",
    "daytime_only": True,
    "daytime_start": "07:00",
    "daytime_end": "19:00",
    "r2_enabled": False,
    "r2_account_id": "",
    "r2_bucket": "",
    "r2_public_base_url": "",
    "r2_key_prefix": "events",
    "status_led_enabled": True,
    "status_led_pin": 24,
    "trigger_button_enabled": True,
    "trigger_button_pin": 23,
    "retention_days": 7,
    "healthcheck_url": "",
    "healthcheck_interval_seconds": 300,
    "delete_after_upload": False,
    "realtime_enabled": False,
    "realtime_key_prefix": "realtime",
    "realtime_window_minutes": 30,
    "realtime_max_image_dim": 0,
}


def _parse_time_of_day(value: object, key: str) -> dtime:
    """Parse a "HH:MM" (or "HH:MM:SS") config value into a datetime.time.

    Unquoted YAML like `07:00` is resolved by PyYAML as a sexagesimal integer
    (420), so reject ints with a message pointing at the quotes rather than
    silently mis-parsing the window.
    """
    if isinstance(value, dtime):
        return value.replace(microsecond=0)
    if isinstance(value, int):
        raise RuntimeError(
            f"{key} must be a quoted \"HH:MM\" string (got {value!r}). "
            f"Unquoted times like 07:00 are read as numbers by YAML."
        )
    text = str(value).strip()
    parts = text.split(":")
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        raise RuntimeError(f'{key} must be a "HH:MM" 24-hour time (got {value!r})')
    hour, minute = int(parts[0]), int(parts[1])
    second = int(parts[2]) if len(parts) == 3 else 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        raise RuntimeError(f'{key} must be a valid 24-hour time (got {value!r})')
    return dtime(hour, minute, second)


def load_config(yaml_path: Path | str = "config.yaml") -> Config:
    load_dotenv()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set (check .env)")
    rtsp_url = os.environ.get("RTSP_URL")
    if not rtsp_url:
        raise RuntimeError("RTSP_URL is not set (check .env)")

    yaml_path = Path(yaml_path)
    data: dict = {}
    if yaml_path.exists():
        with yaml_path.open() as f:
            data = yaml.safe_load(f) or {}

    merged = {**DEFAULTS, **data}

    if "video_duration" in data:
        logging.getLogger("bird_away").warning(
            "config.yaml contains 'video_duration' which is no longer used. "
            "Replace it with 'pre_spray_seconds' and 'post_spray_seconds'. "
            "See config.yaml.example for details."
        )

    daytime_start = _parse_time_of_day(merged["daytime_start"], "daytime_start")
    daytime_end = _parse_time_of_day(merged["daytime_end"], "daytime_end")
    if bool(merged["daytime_only"]) and daytime_start == daytime_end:
        raise RuntimeError(
            "daytime_start and daytime_end are identical, which would disable "
            "detection entirely. Set daytime_only: false to run 24/7."
        )

    if bool(merged["r2_enabled"]):
        missing = [
            k for k in ("r2_account_id", "r2_bucket", "r2_public_base_url")
            if not str(merged.get(k, "")).strip()
        ]
        if missing:
            raise RuntimeError(
                f"r2_enabled is true but these config keys are empty: {', '.join(missing)}"
            )

    if bool(merged["delete_after_upload"]) and not bool(merged["r2_enabled"]):
        raise RuntimeError(
            "delete_after_upload requires r2_enabled: true (otherwise captures "
            "would be deleted with no remote copy)"
        )

    if bool(merged["realtime_enabled"]) and not bool(merged["r2_enabled"]):
        raise RuntimeError(
            "realtime_enabled requires r2_enabled: true (real-time frames are "
            "uploaded straight to R2 with no local copy)"
        )

    return Config(
        openrouter_api_key=api_key,
        rtsp_url=rtsp_url,
        interval_seconds=int(merged["interval_seconds"]),
        spray_duration=float(merged["spray_duration"]),
        pre_spray_seconds=float(merged["pre_spray_seconds"]),
        post_spray_seconds=float(merged["post_spray_seconds"]),
        gpio_pin=int(merged["gpio_pin"]),
        relay_active_high=bool(merged["relay_active_high"]),
        capture_dir=Path(merged["capture_dir"]).expanduser().resolve(),
        detector_model=str(merged["detector_model"]),
        detector_base_url=str(merged["detector_base_url"]),
        detector_prompt=str(merged["detector_prompt"]),
        detector_max_image_dim=int(merged["detector_max_image_dim"]),
        motion_enabled=bool(merged["motion_enabled"]),
        motion_threshold=float(merged["motion_threshold"]),
        motion_downscale=int(merged["motion_downscale"]),
        log_level=str(merged["log_level"]).upper(),
        daytime_only=bool(merged["daytime_only"]),
        daytime_start=daytime_start,
        daytime_end=daytime_end,
        r2_enabled=bool(merged["r2_enabled"]),
        r2_account_id=str(merged["r2_account_id"]),
        r2_bucket=str(merged["r2_bucket"]),
        r2_public_base_url=str(merged["r2_public_base_url"]),
        r2_key_prefix=str(merged["r2_key_prefix"]).strip("/"),
        status_led_enabled=bool(merged["status_led_enabled"]),
        status_led_pin=int(merged["status_led_pin"]),
        trigger_button_enabled=bool(merged["trigger_button_enabled"]),
        trigger_button_pin=int(merged["trigger_button_pin"]),
        retention_days=int(merged["retention_days"]),
        healthcheck_url=str(merged["healthcheck_url"]).strip(),
        healthcheck_interval_seconds=int(merged["healthcheck_interval_seconds"]),
        delete_after_upload=bool(merged["delete_after_upload"]),
        realtime_enabled=bool(merged["realtime_enabled"]),
        realtime_key_prefix=str(merged["realtime_key_prefix"]).strip("/"),
        realtime_window_minutes=int(merged["realtime_window_minutes"]),
        realtime_max_image_dim=int(merged["realtime_max_image_dim"]),
    )
