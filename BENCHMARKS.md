# Detector model benchmark

Run with `scripts/benchmark_models.py` against the 22 labeled fixtures in
`test_images/` (8 bird, 14 no-bird). Numbers below are from 2026-09-06,
5 calls per image per model (110 calls per model), native camera
resolution, the production `detector_prompt`, and `detector_max_tokens: 64`.

Cost is what OpenRouter actually billed per call. `$/month` extrapolates
to 21,600 calls — one frame per minute over a 12-hour active window —
which is the worst case, since `motion_enabled` suppresses most frames.

| Model | acc | recall | FPR | $/call | $/month | median latency |
| --- | --- | --- | --- | --- | --- | --- |
| `google/gemini-3.1-flash-lite` | 95% | 100% | 7% | $0.000290 | $6.27 | 1.30s |
| `openai/gpt-4.1-mini` | 94% | 98% | 9% | $0.000653 | $14.10 | 2.52s |
| `google/gemini-3-flash-preview` (was default) | 92% | 100% | 13% | $0.000581 | $12.55 | 1.44s |
| `qwen/qwen3-vl-32b-instruct` | 91% | 75% | 0% | $0.000257 | $5.54 | 1.21s |
| `google/gemini-2.5-flash-lite` | 89% | 80% | 6% | $0.000185 | $3.99 | 1.78s |
| `google/gemini-2.5-flash` | 87% | 90% | 14% | $0.000541 | $11.68 | 1.78s |

A wider first pass (16 models, 1 call per image, at native / 1280 / 768px)
ruled out the rest. `google/gemma-3-27b-it` is 20x cheaper than anything
here and useless — 36-64% false-positive rate. `amazon/nova-lite-v1`,
`mistralai/mistral-small-3.2-24b-instruct` and `anthropic/claude-haiku-4.5`
all sit at 25-50% recall: they simply do not resolve a 25px bird.
`z-ai/glm-5.3-flash` and `qwen/qwen3-vl-235b-a22b-instruct` returned
malformed or empty completions on a fifth of calls.

## Conclusion

`google/gemini-3.1-flash-lite` is the pick: same recall as the previous
default at half the price, and it is the only model whose errors are
confined to one fixture. It got every other image right 5 times out of 5.

Its lone miss is `pool_lg_no.jpg`, which every model in the table also
misses — the object on the far coping is bird-shaped enough that the
label is arguably wrong. Under the two alternative readings (drop the
four off-distribution `pool_lg_*`/`pool_sm_*` fixtures, or relabel
`pool_lg_no.jpg` as a positive) `gemini-3.1-flash-lite` scores 100%
accuracy with 0% false positives, and still ranks first or tied-first.
The ranking does not depend on which reading you take.

## Two things the numbers do not say

Resolution is not a cost lever on Gemini. Google bills an image as a flat
token block, so `detector_max_image_dim: 768` costs the same $0.000290 as
native. It is purely an accuracy knob, and one that cuts both ways:
downscaling to 1280px lifted `gemini-2.5-flash-lite` from 89% to 94% but
collapsed `openai/gpt-4.1-mini` from 94% to 77% (30% FPR). For non-Gemini
models it is a real 3-6x saving — `qwen/qwen3-vl-32b-instruct` drops from
$0.000257 to $0.000096 per call — but none of those models were accurate
enough for that to matter.

Reasoning models need `detector_max_tokens` raised. At 64 tokens
`openai/gpt-5-nano`, `openai/gpt-5-mini` and `z-ai/glm-5.3-flash` spend the
entire budget thinking and return an empty completion, which the detector
reads as "no bird" — 0% recall that looks like a working detector. They
were re-run at 512 tokens with `reasoning_effort: minimal`; `gpt-5-nano`
still posted a 64-79% false-positive rate.

## Caveats

22 images is small: one flipped image moves accuracy by 4.5 points, so
treat gaps under ~5 points as noise. The eight positives also come from
two sessions in flat light, so recall means "resolves these birds at this
distance in this light" — see `test_images/README.md`. The false-positive
half is better supported, since the negatives span two days of varied
light and include the seven frames the deployed detector actually
false-positived on in production.

## Reproducing

    cp .env.example .env          # set OPENROUTER_API_KEY
    .venv/bin/python scripts/benchmark_models.py --repeats 5 --out results/

`--models` overrides the candidate list in `scripts/benchmark_config.yaml`,
`--max-dim` mirrors `detector_max_image_dim`, and `--out` writes per-call
`calls.csv` plus `summary.json` for post-hoc scoring.
