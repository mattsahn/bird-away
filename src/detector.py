from __future__ import annotations

import base64
import io
import logging

from openai import OpenAI, OpenAIError
from PIL import Image


logger = logging.getLogger(__name__)


def downscale_jpeg(image_bytes: bytes, max_dim: int, quality: int = 80) -> bytes:
    """Downscale a JPEG so its longer edge is at most ``max_dim`` pixels.

    Returns the input unchanged when ``max_dim <= 0`` or the image already
    fits. Re-encodes as JPEG at ``quality`` otherwise.
    """
    if max_dim <= 0:
        return image_bytes
    img = Image.open(io.BytesIO(image_bytes))
    if max(img.size) <= max_dim:
        return image_bytes
    img.thumbnail((max_dim, max_dim))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


class Detector:
    def __init__(
        self,
        api_key: str,
        system_prompt: str,
        model: str = "anthropic/claude-haiku-4.5",
        base_url: str = "https://openrouter.ai/api/v1",
        max_image_dim: int = 512,
        jpeg_quality: int = 80,
    ) -> None:
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._system_prompt = system_prompt
        self._max_image_dim = max_image_dim
        self._jpeg_quality = jpeg_quality

    def prepare_image(self, image_bytes: bytes) -> bytes:
        """Return the JPEG bytes that would be sent to the model.

        Downscales to max_image_dim on the longer edge if the input is
        larger; otherwise returns the input unchanged.
        """
        out = downscale_jpeg(image_bytes, self._max_image_dim, self._jpeg_quality)
        if out is not image_bytes:
            logger.info(
                "detector_image_prepared size_in=%dB size_out=%dB",
                len(image_bytes), len(out),
            )
        return out

    def is_bird_present(self, image_bytes: bytes) -> bool:
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        data_uri = f"data:image/jpeg;base64,{b64}"
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                max_tokens=4,
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_uri}},
                        ],
                    },
                ],
            )
        except OpenAIError:
            logger.exception("openai_api_error")
            return False
        except Exception:
            logger.exception("detector_unexpected_error")
            return False

        text = (resp.choices[0].message.content or "").strip().lower()
        logger.debug("detector_answer", extra={"answer": text})
        return text.startswith("yes")
