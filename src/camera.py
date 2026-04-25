from __future__ import annotations

import re
import subprocess
from pathlib import Path


_CREDS_RE = re.compile(r"(rtsps?://)[^/@\s]+@", re.IGNORECASE)


def _redact(text: str) -> str:
    return _CREDS_RE.sub(r"\1***:***@", text)


class CameraError(RuntimeError):
    pass


def capture_frame(rtsp_url: str, timeout_s: int = 15) -> bytes:
    cmd = [
        "ffmpeg",
        "-rtsp_transport", "tcp",
        "-i", rtsp_url,
        "-frames:v", "1",
        "-f", "image2",
        "-loglevel", "error",
        "-y",
        "-",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise CameraError(
            f"ffmpeg timed out after {timeout_s}s capturing frame"
        ) from None

    if result.returncode != 0 or not result.stdout:
        stderr = _redact(result.stderr.decode("utf-8", errors="replace").strip())
        raise CameraError(
            f"ffmpeg failed (rc={result.returncode}) capturing frame: {stderr}"
        )
    return result.stdout


def start_recording(
    rtsp_url: str,
    output_path: Path,
    duration_s: int,
) -> subprocess.Popen:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-rtsp_transport", "tcp",
        "-i", rtsp_url,
        "-t", str(duration_s),
        "-c:v", "copy",
        "-an",
        "-loglevel", "error",
        "-y",
        str(output_path),
    ]
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
