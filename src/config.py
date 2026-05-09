from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    openrouter_api_key: str
    rtsp_url: str
    interval_seconds: int
    spray_duration: float
    video_duration: int
    gpio_pin: int
    relay_active_high: bool
    capture_dir: Path
    detector_model: str
    detector_base_url: str
    detector_prompt: str
    motion_enabled: bool
    motion_threshold: float
    motion_downscale: int
    log_level: str
    daytime_only: bool
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


DEFAULTS = {
    "interval_seconds": 60,
    "spray_duration": 3,
    "video_duration": 7,
    "gpio_pin": 17,
    "relay_active_high": True,
    "capture_dir": "./captures",
    "detector_model": "anthropic/claude-haiku-4.5",
    "detector_base_url": "https://openrouter.ai/api/v1",
    "detector_prompt": (
        "You are a bird detector for a backyard pool. "
        "Respond with exactly 'yes' if you see one or more birds in, on, or "
        "near the pool (including birds in flight directly above it). "
        "Respond with exactly 'no' otherwise. Output only the single word."
    ),
    "motion_enabled": True,
    "motion_threshold": 5.0,
    "motion_downscale": 320,
    "log_level": "INFO",
    "daytime_only": True,
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
}


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

    return Config(
        openrouter_api_key=api_key,
        rtsp_url=rtsp_url,
        interval_seconds=int(merged["interval_seconds"]),
        spray_duration=float(merged["spray_duration"]),
        video_duration=int(merged["video_duration"]),
        gpio_pin=int(merged["gpio_pin"]),
        relay_active_high=bool(merged["relay_active_high"]),
        capture_dir=Path(merged["capture_dir"]).expanduser().resolve(),
        detector_model=str(merged["detector_model"]),
        detector_base_url=str(merged["detector_base_url"]),
        detector_prompt=str(merged["detector_prompt"]),
        motion_enabled=bool(merged["motion_enabled"]),
        motion_threshold=float(merged["motion_threshold"]),
        motion_downscale=int(merged["motion_downscale"]),
        log_level=str(merged["log_level"]).upper(),
        daytime_only=bool(merged["daytime_only"]),
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
    )
