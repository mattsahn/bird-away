from __future__ import annotations

import io
import logging
import re
import subprocess
import threading
import time
from pathlib import Path

import av
from av.error import FFmpegError


_CREDS_RE = re.compile(r"(rtsps?://)[^/@\s]+@", re.IGNORECASE)


def _redact(text: str) -> str:
    return _CREDS_RE.sub(r"\1***:***@", text)


logger = logging.getLogger(__name__)


class CameraError(RuntimeError):
    pass


class Camera:
    """Persistent RTSP camera with a background decode thread.

    Holds one long-lived RTSP/TCP session. A background thread continuously
    decodes frames; capture_frame() returns the most recently decoded frame
    as JPEG bytes. Reconnects automatically on stream errors.
    """

    def __init__(
        self,
        rtsp_url: str,
        *,
        encode_interval_s: float = 1.0,
        reconnect_delay_s: float = 3.0,
        socket_timeout_s: float = 10.0,
        stale_restart_s: float = 30.0,
        jpeg_quality: int = 85,
    ) -> None:
        self._rtsp_url = rtsp_url
        self._encode_interval_s = encode_interval_s
        self._reconnect_delay_s = reconnect_delay_s
        self._socket_timeout_s = socket_timeout_s
        self._socket_timeout_us = str(int(socket_timeout_s * 1_000_000))
        self._stale_restart_s = stale_restart_s
        self._jpeg_quality = jpeg_quality

        self._latest_jpeg: bytes | None = None
        self._latest_at: float = 0.0
        self._lock = threading.Lock()
        self._stop: threading.Event | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "Camera":
        self.start()
        return self

    def __exit__(self, *_exc_info) -> None:
        self.close()

    def start(self) -> None:
        if self._thread is not None:
            return
        stop = threading.Event()
        self._stop = stop
        self._thread = threading.Thread(
            target=self._run, args=(stop,), name="camera-rtsp", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        if self._stop is not None:
            self._stop.set()
        thread = self._thread
        self._thread = None
        self._stop = None
        if thread is not None:
            thread.join(timeout=5.0)

    def capture_frame(self, max_age_s: float = 5.0, wait_s: float = 10.0) -> bytes:
        """Return the most recently decoded frame as JPEG bytes.

        Blocks up to wait_s for a frame younger than max_age_s. If the
        latest frame is older than stale_restart_s, force-restarts the
        background decode thread once before giving up — guards against
        libav blocking forever on a silent socket.
        """
        deadline = time.monotonic() + wait_s
        restarted = False
        while True:
            with self._lock:
                jpeg = self._latest_jpeg
                latest_at = self._latest_at
            age = (time.monotonic() - latest_at) if jpeg is not None else float("inf")
            if jpeg is not None and age <= max_age_s:
                return jpeg
            if jpeg is not None and not restarted and age >= self._stale_restart_s:
                logger.warning(
                    "camera_force_restart age=%.0fs (decode thread appears stuck)",
                    age,
                )
                self.close()
                self.start()
                restarted = True
                deadline = time.monotonic() + wait_s
            if time.monotonic() >= deadline:
                if jpeg is None:
                    raise CameraError(
                        f"no frame received within {wait_s}s of capture call"
                    )
                raise CameraError(
                    f"latest frame is {age:.1f}s old (max_age={max_age_s}s)"
                )
            time.sleep(0.1)

    def start_recording(
        self,
        output_path: Path,
        duration_s: int,
    ) -> subprocess.Popen:
        """Stop the persistent decode and spawn ffmpeg to record MP4.

        Many cameras only allow one or two concurrent RTSP sessions, so we
        release the decode connection before opening the recording one. The
        caller must invoke resume() after the recording finishes (or fails)
        to restart the persistent decode thread.
        """
        logger.info("camera_pausing_for_recording")
        self.close()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg",
            "-rtsp_transport", "tcp",
            "-i", self._rtsp_url,
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

    def resume(self) -> None:
        """Restart the persistent decode thread (call after start_recording)."""
        logger.info("camera_resuming_after_recording")
        self.start()

    def _run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self._stream_loop(stop)
                if not stop.is_set():
                    logger.warning(
                        "camera_stream_eof reconnecting_in=%.1fs",
                        self._reconnect_delay_s,
                    )
            except FFmpegError as e:
                logger.warning(
                    "camera_stream_error %s reconnecting_in=%.1fs",
                    type(e).__name__, self._reconnect_delay_s,
                )
            except Exception:
                logger.exception("camera_stream_unexpected reconnecting")
            stop.wait(self._reconnect_delay_s)

    def _stream_loop(self, stop: threading.Event) -> None:
        container = av.open(
            self._rtsp_url,
            options={
                "rtsp_transport": "tcp",
                # libav names: stimeout (FFmpeg <6) and timeout (FFmpeg 6+).
                # Both are accepted; unknown ones are silently ignored.
                "stimeout": self._socket_timeout_us,
                "timeout": self._socket_timeout_us,
            },
            timeout=(self._socket_timeout_s, self._socket_timeout_s),
        )
        try:
            stream = container.streams.video[0]
            stream.codec_context.thread_type = "AUTO"
            logger.info(
                "camera_connected codec=%s size=%dx%d",
                stream.codec_context.name,
                stream.codec_context.width,
                stream.codec_context.height,
            )
            last_encode = 0.0
            for frame in container.decode(stream):
                if stop.is_set():
                    return
                now = time.monotonic()
                if now - last_encode >= self._encode_interval_s:
                    jpeg = self._frame_to_jpeg(frame)
                    with self._lock:
                        self._latest_jpeg = jpeg
                        self._latest_at = now
                    last_encode = now
        finally:
            container.close()

    def _frame_to_jpeg(self, frame) -> bytes:
        img = frame.to_image()
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self._jpeg_quality)
        return buf.getvalue()
