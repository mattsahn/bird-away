from __future__ import annotations

import subprocess
from pathlib import Path


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
    except subprocess.TimeoutExpired as e:
        raise CameraError(f"ffmpeg timed out after {timeout_s}s capturing frame") from e

    if result.returncode != 0 or not result.stdout:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
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
