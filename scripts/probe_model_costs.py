"""Measure what one detector call actually costs on every vision model.

Per-token price does not tell you what a frame costs, because models
tokenize the same image very differently: one provider may bill a 2304x1296
frame as ~260 tokens and another as several thousand. A model with a high
per-token price can therefore be cheaper per call than a cheap-per-token
one, so ranking candidates by catalog price alone hides usable models.

This sends a single real frame to every image-capable model in the
OpenRouter catalog, asking for the billed amount (``usage.include``), and
prints the models sorted by measured cost per call. Feed the ones inside
your budget to ``benchmark_models.py`` to find out whether they can see
the birds.

Catalog prices are still used as a coarse guard: any model whose estimated
cost exceeds ``--max-estimate`` is skipped unprobed, which keeps the frontier
models out of a whole-catalog run. It is an estimate, not a spending cap --
how a model tokenizes an image is exactly the thing that cannot be known
before probing it, and a model that bills the frame as 36,901 tokens will
overrun an estimate built on 4,000 by roughly that ratio. The exposure is
bounded by being one call per model: the worst run observed cost $0.12 total.
The measured numbers this prints are what to trust.

Usage:
    python scripts/probe_model_costs.py
    python scripts/probe_model_costs.py --budget 0.00029 --out results/costs
    python scripts/probe_model_costs.py --max-dim 1280

The OPENROUTER_API_KEY env var is read from .env at the repo root.
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv
from openai import OpenAI

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.detector import downscale_jpeg

MODELS_URL = "https://openrouter.ai/api/v1/models"
DEFAULT_CONFIG = Path(__file__).resolve().parent / "benchmark_config.yaml"
DEFAULT_IMAGE = REPO_ROOT / "test_images" / "pool_yes_20260731T233717Z.jpg"

# Token counts for the pre-probe estimate. A frame has measured anywhere from
# 317 to 36,901 prompt tokens, so no single figure is a bound; this is a
# middling guess, used only to keep frontier pricing out of a catalog sweep.
EST_PROMPT_TOKENS = 4000
EST_COMPLETION_TOKENS = 512  # the --max-tokens default


@dataclass
class Probe:
    model: str
    ok: bool
    cost_usd: float | None
    prompt_tokens: int | None
    completion_tokens: int | None
    estimate_usd: float | None
    answer: str
    elapsed_s: float
    error: str | None = None


def fetch_catalog() -> list[dict]:
    with urllib.request.urlopen(MODELS_URL, timeout=60) as resp:
        return json.load(resp)["data"]


def _price(pricing: dict, key: str) -> float:
    """Catalog price for one unit, or infinity when it is not knowable.

    The router meta-models (``openrouter/auto``) advertise ``-1``, meaning
    "whatever the model it picks charges". Reading that as a negative price
    made them look free and got them probed at $0.006 a call, which is the
    whole failure mode of trusting the catalog.
    """
    try:
        value = float(pricing.get(key) or 0.0)
    except (TypeError, ValueError):
        return float("inf")
    return float("inf") if value < 0 else value


def estimate_cost(model: dict) -> float:
    """Rough cost of one detector call from catalog prices.

    Uses the dearest of the model's price overrides, but assumes
    ``EST_PROMPT_TOKENS`` for the image, so the real call can cost several
    times this. Order-of-magnitude filtering only. Infinity means the catalog
    does not say, which skips the model rather than probing it blind.
    """
    pricing = model.get("pricing") or {}
    overrides = pricing.get("overrides") or []
    prompt = max(
        [_price(pricing, "prompt")] + [_price(o, "prompt") for o in overrides]
    )
    completion = max(
        [_price(pricing, "completion")] + [_price(o, "completion") for o in overrides]
    )
    return (
        prompt * EST_PROMPT_TOKENS
        + completion * EST_COMPLETION_TOKENS
        + _price(pricing, "image")
        + _price(pricing, "request")
    )


def vision_models(catalog: list[dict]) -> list[dict]:
    out = []
    for model in catalog:
        arch = model.get("architecture") or {}
        if "image" not in (arch.get("input_modalities") or []):
            continue
        if "text" not in (arch.get("output_modalities") or ["text"]):
            continue
        out.append(model)
    return out


def probe(
    client: OpenAI, model: dict, prompt: str, data_uri: str, max_tokens: int
) -> Probe:
    model_id = model["id"]
    estimate = estimate_cost(model)
    t0 = time.monotonic()
    try:
        resp = client.chat.completions.create(
            model=model_id,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                },
            ],
            extra_body={"usage": {"include": True}},
        )
    except Exception as e:  # noqa: BLE001 - an unusable model is a result, not a crash
        return Probe(
            model=model_id,
            ok=False,
            cost_usd=None,
            prompt_tokens=None,
            completion_tokens=None,
            estimate_usd=estimate,
            answer="",
            elapsed_s=time.monotonic() - t0,
            error=f"{type(e).__name__}: {e}"[:200],
        )

    elapsed = time.monotonic() - t0
    choice = resp.choices[0] if resp.choices else None
    answer = ((choice.message.content if choice else None) or "").strip()
    usage = resp.usage
    cost = None
    if usage is not None:
        cost = getattr(usage, "cost", None)
        if cost is None:
            cost = (getattr(usage, "model_extra", None) or {}).get("cost")
    return Probe(
        model=model_id,
        ok=True,
        cost_usd=float(cost) if cost is not None else None,
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
        estimate_usd=estimate,
        answer=answer.replace("\n", " ")[:60],
        elapsed_s=elapsed,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--image", type=Path, default=DEFAULT_IMAGE,
        help="Frame to send (default: a positive fixture at camera resolution)",
    )
    parser.add_argument(
        "--max-dim", type=int, default=0,
        help="Downscale longer edge first, like detector_max_image_dim (0 = native)",
    )
    parser.add_argument("--jpeg-quality", type=int, default=80)
    parser.add_argument(
        "--max-tokens", type=int, default=512,
        help="Completion budget, matching detector_max_tokens (default 512). "
             "Reasoning models answer with nothing below it",
    )
    parser.add_argument(
        "--max-estimate", type=float, default=0.02,
        help="Skip models whose estimated cost per call exceeds this many "
             "dollars (default 0.02). A filter, not a spending cap: image "
             "tokenization is unknown until measured, so an accepted probe can "
             "bill several times the estimate",
    )
    parser.add_argument(
        "--budget", type=float, default=0.00029,
        help="Highlight models at or under this measured cost per call "
             "(default 0.00029, the current detector_model)",
    )
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument(
        "--interval-seconds", type=float, default=60.0,
        help="Sampling interval used for the monthly cost estimate",
    )
    parser.add_argument(
        "--active-hours", type=float, default=12.0,
        help="Active hours per day used for the monthly cost estimate",
    )
    parser.add_argument("--out", type=Path, default=None, help="Directory for CSV output")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        sys.exit("OPENROUTER_API_KEY not set (check .env)")

    cfg = yaml.safe_load(args.config.read_text()) if args.config.exists() else {}
    prompt = ((cfg or {}).get("classification_prompt") or "").strip()
    if not prompt:
        sys.exit(f"config {args.config} has no classification_prompt")
    if not args.image.exists():
        sys.exit(f"image not found: {args.image}")

    data = downscale_jpeg(args.image.read_bytes(), args.max_dim, args.jpeg_quality)
    data_uri = "data:image/jpeg;base64," + base64.standard_b64encode(data).decode("ascii")

    catalog = fetch_catalog()
    candidates = vision_models(catalog)
    affordable = [m for m in candidates if estimate_cost(m) <= args.max_estimate]
    skipped = len(candidates) - len(affordable)
    calls_per_month = (args.active_hours * 3600 / args.interval_seconds) * 30
    print(
        f"catalog: {len(catalog)} models, {len(candidates)} accept images, "
        f"probing {len(affordable)} (skipped {skipped} estimated over "
        f"${args.max_estimate:.4f}/call)"
    )
    print(
        f"image: {args.image.name} at {args.max_dim or 'native'} resolution, "
        f"{len(data) / 1024:.0f} KiB"
    )

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
        results = list(
            pool.map(
                lambda m: probe(client, m, prompt, data_uri, args.max_tokens),
                affordable,
            )
        )

    priced = [p for p in results if p.cost_usd is not None]
    priced.sort(key=lambda p: p.cost_usd or 0.0)
    header = (
        f"{'model':52s} {'$/call':>9s} {'$/month':>8s} {'in_tok':>7s} "
        f"{'out':>4s} {'est$':>9s} {'answer':<12s}"
    )
    print("\n" + header)
    print("-" * len(header))
    for p in priced:
        monthly = (p.cost_usd or 0.0) * calls_per_month
        mark = "*" if (p.cost_usd or 0.0) <= args.budget else " "
        print(
            f"{mark}{p.model:51s} {p.cost_usd:9.6f} {monthly:8.2f} "
            f"{p.prompt_tokens or 0:7d} {p.completion_tokens or 0:4d} "
            f"{p.estimate_usd or 0.0:9.6f} {p.answer[:12]:<12s}"
        )
    in_budget = [p for p in priced if (p.cost_usd or 0.0) <= args.budget]
    failed = [p for p in results if not p.ok]
    spent = sum(p.cost_usd or 0.0 for p in priced)
    print(
        f"\n{len(in_budget)} models at or under ${args.budget:.6f}/call (marked *), "
        f"{len(failed)} unreachable, {len(results) - len(priced) - len(failed)} "
        f"returned no cost. This run billed ${spent:.4f} in total."
    )
    print("in budget: " + ",".join(p.model for p in in_budget))

    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        path = args.out / "model_costs.csv"
        with path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "model", "ok", "cost_usd", "monthly_usd", "prompt_tokens",
                    "completion_tokens", "estimate_usd", "answer", "elapsed_s", "error",
                ]
            )
            for p in sorted(results, key=lambda p: (p.cost_usd is None, p.cost_usd or 0)):
                w.writerow(
                    [
                        p.model, int(p.ok),
                        "" if p.cost_usd is None else f"{p.cost_usd:.8f}",
                        "" if p.cost_usd is None else f"{p.cost_usd * calls_per_month:.4f}",
                        p.prompt_tokens or "", p.completion_tokens or "",
                        f"{p.estimate_usd:.8f}" if p.estimate_usd is not None else "",
                        p.answer, f"{p.elapsed_s:.3f}", p.error or "",
                    ]
                )
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
