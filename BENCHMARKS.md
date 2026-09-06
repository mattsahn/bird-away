# Detector model benchmark

Run with `scripts/benchmark_models.py` against the labeled fixtures in
`test_images/` — 24 frames, 8 bird and 16 no-bird as of 2026-09-06. Cost is
what OpenRouter actually billed per call. `$/month` extrapolates to 21,600
calls — one frame per minute over a 12-hour active window — which is the worst
case, since `motion_enabled` suppresses most frames.

Two rounds are recorded here. The first picked a model on price alone and got
a defensible but wrong answer; the second measured what calls actually cost and
found both a cheaper model and a prompt bug worth more than the model choice.

## Result

`bytedance-seed/seed-2.0-mini` with the exclusion-carrying prompt, at
`detector_max_tokens: 512`. Ten calls per image, 240 calls:

| | value |
| --- | --- |
| accuracy | 99% |
| recall | 100% (80/80) |
| false-positive rate | 1% (2/159) |
| $/call | $0.000162 |
| $/month | $3.50 |
| median latency | 1.78s |

Both false positives, and the one blank answer, are on `pool_lg_no.jpg` — the
disputed fixture (see below). It got every other frame right 10 times out of 10.

That is 3.6x cheaper than the original `google/gemini-3-flash-preview`
($12.55/month) and 1.8x cheaper than the `google/gemini-3.1-flash-lite` that
round 1 chose ($6.27/month), with better accuracy than either.

## Round 2: measured cost, and a prompt fix

### Per-token price ranks models wrongly

Models tokenize the same 2304x1296 frame anywhere from 317 to 36,901 tokens, so
the price list is close to useless for a single-image workload.
`scripts/probe_model_costs.py` sends one real frame to every image-capable model
in the catalog and reports the billed amount. The spread against a
catalog-price estimate:

| Model | estimated $/call | measured $/call | |
| --- | --- | --- | --- |
| `google/gemini-3.1-flash-image-preview` | $0.002192 | $0.000161 | 14x cheaper (317 image tokens) |
| `openai/gpt-4o-mini` | $0.000638 | $0.005536 | 9x dearer (36,901 image tokens) |
| `openai/gpt-5-nano` | $0.000226 | $0.000202 | about right |

Of 262 image-capable models, 153 were cheap enough to probe safely and 31 came
in at or under the round-1 winner's $0.00029/call — roughly 20 of which round 1
had excluded on price without ever testing their accuracy.

### The best of those, 5 calls per image, old prompt

| Model | acc | recall | FPR | $/call | $/month | median latency |
| --- | --- | --- | --- | --- | --- | --- |
| `qwen/qwen3.5-flash-02-23` | 95% | 91% | 4% | $0.000263 | $5.69 | 8.78s |
| `google/gemini-3.5-flash-lite` | 94% | 88% | 2% | $0.000349 | $7.54 | 1.04s |
| `bytedance-seed/seed-2.0-mini` | 93% | 100% | 10% | $0.000154 | $3.32 | 1.87s |
| `google/gemini-3.1-flash-lite` (round-1 pick) | 92% | 100% | 12% | $0.000290 | $6.27 | 1.17s |
| `stepfun/step-3.7-flash` | 92% | 81% | 3% | $0.000253 | $5.45 | 1.89s |
| `nex-agi/nex-n2-mini` | 90% | 82% | 6% | $0.000085 | $1.83 | 2.42s |
| `google/gemini-3.1-flash-image-preview` | 83% | 82% | 16% | $0.000161 | $3.49 | 1.47s |

`qwen/qwen3.5-flash-02-23` rate-limited on 43 of 120 calls and answers in ~9s,
which rules it out for a 60-second loop regardless of its score.
`nex-agi/nex-n2-mini` is the cheapest thing that works at all, at 82% recall.
Two dozen other in-budget models were tested and dropped: `rekaai/reka-edge`
(77% FPR), `mistralai/ministral-3b-2512` (56% FPR),
`bytedance-seed/seed-1.6-flash`, `google/gemma-4-26b-a4b-it`,
`google/gemma-4-31b-it` and `amazon/nova-2-lite-v1` (all 25-33% recall — they do
not resolve a 25px bird), and the `:free` tiers, which score well but rate-limit
on a third to half of calls.

### The prompt was costing more accuracy than the model choice

Every model above failed the same two negatives. One is a duck **out on the
lake**, behind the pool fence — and the prompt said to fire on birds "in, on, or
**near** the pool", so the models were arguably right. Naming the exclusions
instead:

```diff
-Respond with exactly 'yes' if you see one or more birds in, on, or near the
-pool (including birds in flight directly above it).
+Respond with exactly 'yes' if you see one or more birds on the pool deck, in
+the pool water, or in flight directly above it. Birds beyond the fence — on the
+lake, on the far bank, on neighbouring roofs — do not count, and neither do
+towels, cushions, planters or pool toys.
```

Same models, same frames, 5 calls per image:

| Model | acc | recall | FPR | | $/call |
| --- | --- | --- | --- | --- | --- |
| `bytedance-seed/seed-2.0-mini` | 93% → **100%** | 100% → 100% | 10% → **0%** | | $0.000154 → $0.000161 |
| `google/gemini-3.5-flash-lite` | 94% → **98%** | 88% → 98% | 2% → 1% | | $0.000349 → $0.000360 |
| `google/gemini-3.1-flash-lite` | 92% → **96%** | 100% → 100% | 12% → 6% | | $0.000290 → $0.000300 |

The 45 extra prompt tokens cost 3-4% more per call, because the image is ~1,100
of the ~1,200 input tokens. Cheapest accuracy in the whole exercise.

### The catch: blank answers

`seed-2.0-mini` reasons before it answers, and the reasoning is billed but not
visible. At the old `detector_max_tokens: 64` it spent the entire budget
thinking and returned an **empty** completion on 7 of 120 calls — 4 of them on
frames that did contain a bird, which `is_bird_present` reads as "no bird" and
skips the sprinkler, with nothing in the logs but a warning. Raising the budget
to 512 drops that to 1 in 240 and costs nothing measurable ($0.000160 vs
$0.000162), since the answer is still one token. `src/detector.py` now also
retries once before failing closed, which puts the residual rate near
1-in-50,000 calls.

This is the second time this failure mode has bitten this project (see the
`detector_max_tokens: 4` note in `config.yaml.example`). Any model swap should
check for empty completions before trusting a recall number.

## Round 1: the price-filtered comparison

For the record — 5 calls per image, native resolution, old prompt,
`detector_max_tokens: 64`, candidates shortlisted by per-token price:

| Model | acc | recall | FPR | $/call | $/month | median latency |
| --- | --- | --- | --- | --- | --- | --- |
| `google/gemini-3.1-flash-lite` | 95% | 100% | 7% | $0.000290 | $6.27 | 1.30s |
| `openai/gpt-4.1-mini` | 94% | 98% | 9% | $0.000653 | $14.10 | 2.52s |
| `google/gemini-3-flash-preview` (original) | 92% | 100% | 13% | $0.000581 | $12.55 | 1.44s |
| `qwen/qwen3-vl-32b-instruct` | 91% | 75% | 0% | $0.000257 | $5.54 | 1.21s |
| `google/gemini-2.5-flash-lite` | 89% | 80% | 6% | $0.000185 | $3.99 | 1.78s |
| `google/gemini-2.5-flash` | 87% | 90% | 14% | $0.000541 | $11.68 | 1.78s |

(These are over 22 fixtures; two negatives were added afterwards.)
`google/gemma-3-27b-it` is 20x cheaper than anything here and useless — 36-64%
FPR. `amazon/nova-lite-v1`, `mistralai/mistral-small-3.2-24b-instruct` and
`anthropic/claude-haiku-4.5` sit at 25-50% recall.

Resolution is not a cost lever on Gemini: Google bills an image as a flat token
block, so `detector_max_image_dim: 768` costs the same as native. It is purely
an accuracy knob, and it cuts both ways — downscaling to 1280px lifted
`gemini-2.5-flash-lite` from 89% to 94% but collapsed `openai/gpt-4.1-mini` from
94% to 77%. For non-Gemini models it is a real 3-6x saving
(`qwen/qwen3-vl-32b-instruct`: $0.000257 → $0.000096), but no model that cheap
was accurate enough for it to matter.

## The disputed fixture

`pool_lg_no.jpg` is labeled no-bird, and essentially every model tested calls it
a bird. Zoomed in, the object on the far coping does look like one; the label is
probably wrong. It is left as-is because relabeling a fixture to make a number
go up is how benchmarks stop meaning anything — but it is worth knowing that it
accounts for **all** remaining error in the headline result. Relabel it and
`seed-2.0-mini` is 100%/100%/0% over 240 calls.

## Caveats

24 images is small: one flipped frame moves accuracy by ~4 points, so treat gaps
under ~5 points as noise. The eight positives come from two sessions in flat
light, so recall means "resolves these birds at this distance in this light" —
see `test_images/README.md`. The false-positive half is better supported: the
negatives span several days of varied light and include the frames the deployed
detector actually false-positived on in production.

Nothing here measures the motion gate, the sprinkler, or end-to-end latency —
only the classification call.

## Reproducing

    cp .env.example .env          # set OPENROUTER_API_KEY

    # which models are in budget, by measured cost
    .venv/bin/python scripts/probe_model_costs.py --budget 0.00029 --out results/

    # how accurate they are
    .venv/bin/python scripts/benchmark_models.py --repeats 5 --out results/

    # whether a prompt rewording helps
    .venv/bin/python scripts/benchmark_models.py --prompt-file my_prompt.txt

`--models` overrides the candidate list in `scripts/benchmark_config.yaml`,
`--max-dim` mirrors `detector_max_image_dim`, `--max-tokens` mirrors
`detector_max_tokens`, and `--out` writes per-call `calls.csv` plus
`summary.json` for post-hoc scoring — including which fixtures a model got
wrong, which is usually the interesting part.
