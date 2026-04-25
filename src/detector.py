from __future__ import annotations

import base64
import logging

from openai import OpenAI, OpenAIError


logger = logging.getLogger(__name__)


class Detector:
    def __init__(
        self,
        api_key: str,
        system_prompt: str,
        model: str = "anthropic/claude-haiku-4.5",
        base_url: str = "https://openrouter.ai/api/v1",
    ) -> None:
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._system_prompt = system_prompt

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
