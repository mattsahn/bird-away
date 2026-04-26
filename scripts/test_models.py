"""Compare vision models on a single local image.

Runs two prompts per model — a yes/no classifier and a freeform
description — and prints the raw responses side-by-side so you can
see how each model handles the same input.

Usage:
    python scripts/test_models.py path/to/image.jpg
    python scripts/test_models.py path/to/image.jpg --config other_models.yaml

Models and prompts come from scripts/models_config.yaml by default.
The OPENROUTER_API_KEY env var is read from .env at the repo root.
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv
from openai import OpenAI, OpenAIError

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_CONFIG = Path(__file__).resolve().parent / "models_config.yaml"


def _load_config(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"config not found: {path}")
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    if not data.get("models"):
        sys.exit(f"config {path} has no 'models' list")
    return data


def _ask(
    client: OpenAI,
    model: str,
    system_prompt: str,
    data_uri: str,
    max_tokens: int,
) -> tuple[str, dict]:
    t0 = time.monotonic()
    try:
        resp = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                },
            ],
        )
    except OpenAIError as e:
        return f"<API error: {type(e).__name__}: {e}>", {
            "elapsed_s": time.monotonic() - t0,
        }
    except Exception as e:
        return f"<unexpected error: {type(e).__name__}: {e}>", {
            "elapsed_s": time.monotonic() - t0,
        }

    text = (resp.choices[0].message.content or "").strip()
    meta = {"elapsed_s": time.monotonic() - t0}
    if resp.usage is not None:
        meta["prompt_tokens"] = resp.usage.prompt_tokens
        meta["completion_tokens"] = resp.usage.completion_tokens
        meta["total_tokens"] = resp.usage.total_tokens
    return text, meta


def _format_meta(meta: dict) -> str:
    parts = [f"{meta['elapsed_s']:.2f}s"]
    if "total_tokens" in meta:
        parts.append(
            f"{meta['prompt_tokens']}+{meta['completion_tokens']}={meta['total_tokens']} tok"
        )
    return ", ".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path, help="Path to image file")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"Test config (default: {DEFAULT_CONFIG.relative_to(REPO_ROOT)})",
    )
    args = parser.parse_args()

    if not args.image.exists():
        sys.exit(f"image not found: {args.image}")

    load_dotenv(REPO_ROOT / ".env")
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        sys.exit("OPENROUTER_API_KEY not set (check .env)")

    cfg = _load_config(args.config)
    models = cfg["models"]
    classification_prompt = cfg.get("classification_prompt", "").strip()
    description_prompt = cfg.get("description_prompt", "").strip()
    classification_max_tokens = int(cfg.get("classification_max_tokens", 4))
    description_max_tokens = int(cfg.get("description_max_tokens", 500))

    image_bytes = args.image.read_bytes()
    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    data_uri = f"data:image/jpeg;base64,{b64}"

    client = OpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )

    bar = "=" * 72
    print(bar)
    print(f"image:  {args.image} ({len(image_bytes)} bytes)")
    print(f"config: {args.config}")
    print(f"models: {len(models)}")
    print(bar)

    for model in models:
        print(f"\n--- {model} ---")

        cls_text, cls_meta = _ask(
            client, model, classification_prompt, data_uri, classification_max_tokens
        )
        print(f"\n[classification] ({_format_meta(cls_meta)})")
        print(cls_text)

        desc_text, desc_meta = _ask(
            client, model, description_prompt, data_uri, description_max_tokens
        )
        print(f"\n[description] ({_format_meta(desc_meta)})")
        print(desc_text)

    print(f"\n{bar}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
