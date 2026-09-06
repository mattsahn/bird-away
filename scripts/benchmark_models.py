"""Score vision models on the whole labeled test-image set.

Where ``test_models.py`` shows raw answers for one image, this runs the
yes/no detector prompt across every labeled fixture for every model and
reports accuracy, recall, false-positive rate, latency and real cost, so
models can be ranked on the price/accuracy tradeoff.

Ground truth comes from the filename: ``*_yes_*``/``*_yes.jpg`` means a
bird is present, ``*_no_*``/``*_no.jpg`` means none is. Images are
downscaled with the same helper the daemon uses, so ``--max-dim`` matches
``detector_max_image_dim`` in ``config.yaml``.

Cost is whatever OpenRouter actually billed (``usage.include``), summed
over the run and extrapolated to a monthly figure for the sampling rate
in ``--interval-seconds`` / ``--active-hours``.

Reasoning models spend the completion budget before emitting a visible
token and score as all-empty at the daemon's 64-token default, so a model
entry in the config may be a mapping carrying its own ``max_tokens`` and
``reasoning_effort`` instead of a bare id.

Usage:
    python scripts/benchmark_models.py
    python scripts/benchmark_models.py --max-dim 1280 --repeats 3
    python scripts/benchmark_models.py --models google/gemini-2.5-flash-lite,openai/gpt-5-mini
    python scripts/benchmark_models.py --out results/run1
    python scripts/benchmark_models.py --prompt-file prompts/ignore_lake.txt

Models and prompt come from scripts/benchmark_config.yaml by default.
The OPENROUTER_API_KEY env var is read from .env at the repo root.
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv
from openai import OpenAI

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.detector import downscale_jpeg

DEFAULT_CONFIG = Path(__file__).resolve().parent / "benchmark_config.yaml"
DEFAULT_IMAGE_DIR = REPO_ROOT / "test_images"


def label_of(path: Path) -> bool | None:
    """True if the filename says a bird is present, False if not, None if unlabeled."""
    stem = path.stem.lower()
    parts = stem.split("_")
    if "yes" in parts:
        return True
    if "no" in parts:
        return False
    return None


@dataclass(frozen=True)
class ModelSpec:
    id: str
    max_tokens: int
    reasoning_effort: str | None = None

    @property
    def label(self) -> str:
        if self.reasoning_effort:
            return f"{self.id} (reasoning={self.reasoning_effort})"
        return self.id


@dataclass
class Call:
    model: str
    image: str
    truth: bool
    answer: str
    predicted: bool | None
    elapsed_s: float
    prompt_tokens: int | None
    completion_tokens: int | None
    cost_usd: float | None
    error: str | None = None


@dataclass
class Summary:
    model: str
    calls: list[Call] = field(default_factory=list)

    @property
    def scored(self) -> list[Call]:
        return [c for c in self.calls if c.predicted is not None]

    @property
    def errors(self) -> int:
        return sum(1 for c in self.calls if c.error)

    @property
    def unparsed(self) -> int:
        return sum(1 for c in self.calls if c.error is None and c.predicted is None)

    def _count(self, truth: bool, pred: bool) -> int:
        return sum(1 for c in self.scored if c.truth is truth and c.predicted is pred)

    @property
    def tp(self) -> int:
        return self._count(True, True)

    @property
    def fn(self) -> int:
        return self._count(True, False)

    @property
    def fp(self) -> int:
        return self._count(False, True)

    @property
    def tn(self) -> int:
        return self._count(False, False)

    @property
    def accuracy(self) -> float | None:
        n = len(self.scored)
        return (self.tp + self.tn) / n if n else None

    @property
    def recall(self) -> float | None:
        pos = self.tp + self.fn
        return self.tp / pos if pos else None

    @property
    def fpr(self) -> float | None:
        neg = self.fp + self.tn
        return self.fp / neg if neg else None

    @property
    def precision(self) -> float | None:
        pred_pos = self.tp + self.fp
        return self.tp / pred_pos if pred_pos else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        if not p or not r:
            return None
        return 2 * p * r / (p + r)

    @property
    def median_latency(self) -> float | None:
        lat = [c.elapsed_s for c in self.calls if c.error is None]
        return statistics.median(lat) if lat else None

    @property
    def cost_per_call(self) -> float | None:
        costs = [c.cost_usd for c in self.calls if c.cost_usd is not None]
        return sum(costs) / len(costs) if costs else None


def _ask(
    client: OpenAI,
    spec: ModelSpec,
    prompt: str,
    data_uri: str,
    truth: bool,
    image_name: str,
) -> Call:
    model = spec.label
    extra_body: dict = {"usage": {"include": True}}
    if spec.reasoning_effort:
        extra_body["reasoning"] = {"effort": spec.reasoning_effort}
    t0 = time.monotonic()
    try:
        resp = client.chat.completions.create(
            model=spec.id,
            max_tokens=spec.max_tokens,
            messages=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                },
            ],
            extra_body=extra_body,
        )
    except Exception as e:  # noqa: BLE001 - any failure is reported as a failed call
        return Call(
            model=model,
            image=image_name,
            truth=truth,
            answer="",
            predicted=None,
            elapsed_s=time.monotonic() - t0,
            prompt_tokens=None,
            completion_tokens=None,
            cost_usd=None,
            error=f"{type(e).__name__}: {e}",
        )

    elapsed = time.monotonic() - t0
    choice = resp.choices[0] if resp.choices else None
    text = ((choice.message.content if choice else None) or "").strip()
    lowered = text.lower()
    if lowered.startswith("yes"):
        predicted: bool | None = True
    elif lowered.startswith("no"):
        predicted = False
    else:
        predicted = None

    usage = resp.usage
    cost = None
    if usage is not None:
        cost = getattr(usage, "cost", None)
        if cost is None:
            details = getattr(usage, "model_extra", None) or {}
            cost = details.get("cost")
    return Call(
        model=model,
        image=image_name,
        truth=truth,
        answer=text,
        predicted=predicted,
        elapsed_s=elapsed,
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
        cost_usd=float(cost) if cost is not None else None,
    )


def _fmt(value: float | None, spec: str = ".3f") -> str:
    return "n/a" if value is None else format(value, spec)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def print_table(summaries: list[Summary], calls_per_month: float) -> list[dict]:
    rows = []
    for s in summaries:
        monthly = s.cost_per_call * calls_per_month if s.cost_per_call else None
        rows.append(
            {
                "model": s.model,
                "accuracy": s.accuracy,
                "recall": s.recall,
                "fpr": s.fpr,
                "precision": s.precision,
                "f1": s.f1,
                "tp": s.tp,
                "fn": s.fn,
                "fp": s.fp,
                "tn": s.tn,
                "unparsed": s.unparsed,
                "errors": s.errors,
                "median_latency_s": s.median_latency,
                "cost_per_call_usd": s.cost_per_call,
                "monthly_usd": monthly,
            }
        )

    rows.sort(key=lambda r: (-(r["accuracy"] or -1), r["cost_per_call_usd"] or 1e9))
    header = (
        f"{'model':42s} {'acc':>5s} {'recall':>6s} {'FPR':>5s} {'F1':>5s} "
        f"{'TP':>3s} {'FN':>3s} {'FP':>3s} {'TN':>3s} {'err':>4s} "
        f"{'lat_s':>6s} {'$/call':>9s} {'$/month':>8s}"
    )
    print("\n" + header)
    print("-" * len(header))
    for r in rows:
        print(
            f"{r['model']:42s} {_pct(r['accuracy']):>5s} {_pct(r['recall']):>6s} "
            f"{_pct(r['fpr']):>5s} {_fmt(r['f1'], '.2f'):>5s} "
            f"{r['tp']:3d} {r['fn']:3d} {r['fp']:3d} {r['tn']:3d} "
            f"{r['errors'] + r['unparsed']:4d} "
            f"{_fmt(r['median_latency_s'], '.2f'):>6s} "
            f"{_fmt(r['cost_per_call_usd'], '.6f'):>9s} "
            f"{_fmt(r['monthly_usd'], '.2f'):>8s}"
        )
    return rows


def write_outputs(out_dir: Path, calls: list[Call], rows: list[dict], meta: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "calls.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "model", "image", "truth", "predicted", "answer", "elapsed_s",
                "prompt_tokens", "completion_tokens", "cost_usd", "error",
            ]
        )
        for c in calls:
            w.writerow(
                [
                    c.model, c.image, int(c.truth),
                    "" if c.predicted is None else int(c.predicted),
                    c.answer.replace("\n", " "), f"{c.elapsed_s:.3f}",
                    c.prompt_tokens or "", c.completion_tokens or "",
                    "" if c.cost_usd is None else f"{c.cost_usd:.8f}",
                    c.error or "",
                ]
            )
    with (out_dir / "summary.json").open("w") as f:
        json.dump({"meta": meta, "models": rows}, f, indent=2)
    print(f"\nwrote {out_dir / 'calls.csv'} and {out_dir / 'summary.json'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--images", type=Path, default=DEFAULT_IMAGE_DIR,
        help="Directory of labeled images (default: test_images/)",
    )
    parser.add_argument(
        "--models", default="",
        help="Comma-separated model ids, overriding the config list",
    )
    parser.add_argument(
        "--max-dim", type=int, default=0,
        help="Downscale longer edge before sending, like detector_max_image_dim "
             "(default 0 = native resolution)",
    )
    parser.add_argument("--jpeg-quality", type=int, default=80)
    parser.add_argument(
        "--max-tokens", type=int, default=64,
        help="Completion budget for models without a config override, matching "
             "detector_max_tokens (default 64)",
    )
    parser.add_argument(
        "--reasoning-effort", default=None,
        help="Reasoning effort for models without a config override",
    )
    parser.add_argument(
        "--repeats", type=int, default=1,
        help="Calls per image per model, to measure run-to-run flakiness",
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
    parser.add_argument(
        "--prompt-file", type=Path, default=None,
        help="Read the classification prompt from this file instead of the config, "
             "to score prompt wording changes against the same fixtures",
    )
    parser.add_argument("--out", type=Path, default=None, help="Directory for CSV/JSON output")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        sys.exit("OPENROUTER_API_KEY not set (check .env)")

    if not args.config.exists():
        sys.exit(f"config not found: {args.config}")
    cfg = yaml.safe_load(args.config.read_text()) or {}
    raw_models = (
        [m.strip() for m in args.models.split(",") if m.strip()]
        if args.models
        else cfg.get("models") or []
    )
    specs: list[ModelSpec] = []
    for entry in raw_models:
        if isinstance(entry, str):
            entry = {"id": entry}
        if not entry.get("id"):
            sys.exit(f"model entry missing 'id': {entry}")
        specs.append(
            ModelSpec(
                id=entry["id"],
                max_tokens=int(entry.get("max_tokens", args.max_tokens)),
                reasoning_effort=entry.get("reasoning_effort", args.reasoning_effort),
            )
        )
    if not specs:
        sys.exit("no models to test")
    if args.prompt_file:
        if not args.prompt_file.exists():
            sys.exit(f"prompt file not found: {args.prompt_file}")
        prompt = args.prompt_file.read_text().strip()
    else:
        prompt = (cfg.get("classification_prompt") or "").strip()
    if not prompt:
        sys.exit(
            f"no classification_prompt in {args.prompt_file or args.config}"
        )

    images: list[tuple[Path, bool]] = []
    for path in sorted(args.images.iterdir()):
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            continue
        truth = label_of(path)
        if truth is None:
            print(f"skipping unlabeled image: {path.name}")
            continue
        images.append((path, truth))
    if not images:
        sys.exit(f"no labeled images found in {args.images}")

    prepared: list[tuple[str, bool, str]] = []
    for path, truth in images:
        raw = path.read_bytes()
        data = downscale_jpeg(raw, args.max_dim, args.jpeg_quality)
        b64 = base64.standard_b64encode(data).decode("ascii")
        prepared.append((path.name, truth, f"data:image/jpeg;base64,{b64}"))

    n_pos = sum(1 for _, t in images if t)
    calls_per_month = (args.active_hours * 3600 / args.interval_seconds) * 30
    print(
        f"images: {len(images)} ({n_pos} bird / {len(images) - n_pos} no-bird)  "
        f"max_dim={args.max_dim or 'native'}  repeats={args.repeats}  "
        f"models={len(specs)}  calls={len(images) * args.repeats * len(specs)}"
    )
    print(
        f"monthly estimate assumes {calls_per_month:,.0f} calls/month "
        f"({args.active_hours}h/day at {args.interval_seconds:.0f}s interval)"
    )

    client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
    all_calls: list[Call] = []
    summaries: list[Summary] = []
    for spec in specs:
        jobs = [
            (name, truth, uri)
            for _ in range(args.repeats)
            for name, truth, uri in prepared
        ]
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            results = list(
                pool.map(
                    lambda job, spec=spec: _ask(
                        client, spec, prompt, job[2], job[1], job[0]
                    ),
                    jobs,
                )
            )
        s = Summary(model=spec.label, calls=results)
        summaries.append(s)
        all_calls.extend(results)
        print(
            f"  {spec.label:42s} acc={_pct(s.accuracy)} recall={_pct(s.recall)} "
            f"fpr={_pct(s.fpr)} errors={s.errors + s.unparsed}"
        )
        if s.errors:
            first = next(c for c in s.calls if c.error)
            print(f"    first error: {first.error[:160]}")

    rows = print_table(summaries, calls_per_month)
    if args.out:
        write_outputs(
            args.out,
            all_calls,
            rows,
            {
                "max_dim": args.max_dim,
                "jpeg_quality": args.jpeg_quality,
                "max_tokens": args.max_tokens,
                "repeats": args.repeats,
                "images": len(images),
                "positives": n_pos,
                "prompt": prompt,
                "interval_seconds": args.interval_seconds,
                "active_hours": args.active_hours,
                "calls_per_month": calls_per_month,
            },
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
