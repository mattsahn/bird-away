from __future__ import annotations

import base64
import io
import logging

from openai import OpenAI, OpenAIError
from PIL import Image


logger = logging.getLogger(__name__)


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

    def _downscale(self, image_bytes: bytes) -> bytes:
        if self._max_image_dim <= 0:
            return image_bytes
        img = Image.open(io.BytesIO(image_bytes))
        if max(img.size) <= self._max_image_dim:
            return image_bytes
        img.thumbnail((self._max_image_dim, self._max_image_dim))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=self._jpeg_quality)
        return buf.getvalue()

    def is_bird_present(self, image_bytes: bytes) -> bool:
        small = self._downscale(image_bytes)
        logger.info(
            "detector_image size_in=%dB size_out=%dB",
            len(image_bytes), len(small),
        )
        b64 = base64.standard_b64encode(small).decode("ascii")
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
