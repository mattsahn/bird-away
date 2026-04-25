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
    motion_enabled: bool
    motion_threshold: float
    motion_downscale: int
    log_level: str


DEFAULTS = {
    "interval_seconds": 60,
    "spray_duration": 3,
    "video_duration": 30,
    "gpio_pin": 17,
    "relay_active_high": True,
    "capture_dir": "./captures",
    "detector_model": "anthropic/claude-haiku-4.5",
    "detector_base_url": "https://openrouter.ai/api/v1",
    "motion_enabled": True,
    "motion_threshold": 5.0,
    "motion_downscale": 320,
    "log_level": "INFO",
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
        motion_enabled=bool(merged["motion_enabled"]),
        motion_threshold=float(merged["motion_threshold"]),
        motion_downscale=int(merged["motion_downscale"]),
        log_level=str(merged["log_level"]).upper(),
    )
