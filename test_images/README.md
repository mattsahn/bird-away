# Test images

Labeled frames from the pool camera, used to compare vision models with
`scripts/test_models.py` and to smoke-test the detector with
`scripts/test_detector.py`.

Ground truth is in the filename. Nothing here is generated at runtime — these
are fixed fixtures, so a model comparison is reproducible across runs.

## Naming

    pool_yes_<UTC timestamp>.jpg    one or more birds present
    pool_no_<UTC timestamp>.jpg     no birds present

The timestamp matches the source frame's key under the R2 `realtime/frames/`
prefix, so any frame here can be traced back to the event that produced it.

Four older fixtures predate this convention and use
`pool_{lg,sm}_{yes,no}[_N].jpg`, where `lg`/`sm` describe the apparent size of
the bird in frame rather than the image dimensions.

## The 2026-07-31 / 08-01 benchmark set

18 frames, all 2304x1296, all from the same fixed camera position.

| Set | Count | Source |
| --- | --- | --- |
| `pool_yes_20260731T23*` | 6 | 19:25-19:38 local, overcast evening |
| `pool_no_20260731T*` | 5 | 10:48-19:23 local, mostly bright midday |
| `pool_no_20260801T*` | 7 | 12:32-13:33 local, bright midday |

The six positives are small dark birds roughly 20-30 px wide, on the paver deck
near the far coping or on the right-hand deck by the covered patio. One
(`233115Z`) has a bird in the water, which is the easiest positive in the set.
`233717Z` has the largest group, about four birds together.

The seven `20260801` negatives are frames the deployed detector false-positived
on in production. They are the reason the negative set is worth keeping: with
only the five original negatives, `gemini-2.5-flash` measured 0% false-positive
rate, and with all twelve it measures 42%.

One negative is worth calling out. `pool_no_20260731T232314Z.jpg` was captured
at 19:23, two minutes before the first positive, in the same flat grey light.
The rest of the negatives are daylight frames, so without it a model could score
well by keying on "dim frame -> bird" rather than on birds. It is the control
that rules that out.

## Caveats

The six positives all come from a single 13-minute window with the same birds
and the same lighting, so recall measured against this set says "resolves these
birds at this distance in this light", not "resolves birds generally". The
negatives span two days and a wider range of light, so false-positive rate is
the better-supported half. More positives from other times of day are the most
useful thing to add.

## Running a comparison

    cp scripts/models_config.yaml.example scripts/models_config.yaml
    .venv/bin/python scripts/test_models.py test_images/pool_yes_20260731T233717Z.jpg

Note that `scripts/test_models.py` sends the image at whatever resolution it
finds on disk. The daemon applies `detector_max_image_dim` first, so to
reproduce what production sees, downscale with `src.detector.downscale_jpeg`
before running the comparison — resolution changes the answers substantially.
