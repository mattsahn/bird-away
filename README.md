# bird-away

A Raspberry Pi service that watches a pool through an RTSP camera, asks Claude
whether birds are present, and triggers a sprinkler (via relay + solenoid valve)
to startle them. Each detection saves a still image and a short video clip,
and can optionally publish both to a Cloudflare R2 bucket so they're viewable
from anywhere on the internet.

## Hardware

- Raspberry Pi 4 (or earlier) on Wi-Fi.
- RTSP-capable IP camera reachable on the same network.
- Relay module wired to one Pi GPIO pin and ground. Active-high or active-low
  is configurable.
- Solenoid valve on its own power supply, switched by the relay. The Pi must
  not source current to the valve directly.
- Sprinkler aimed to spray over and around the pool when the valve opens.
- Optional: a status LED (e.g. a panel-mount illuminated momentary switch).
  Anode → its own GPIO pin (default `24`), cathode → ground. Heartbeat-blinks
  while the service runs and pulses on photo / bird events.
- Optional: a momentary switch (the contact pair on the same panel-mount
  button) for a manual sprinkler trigger. One contact → GPIO pin (default
  `23`), other → ground; the Pi's internal pull-up holds it HIGH at rest. The
  status LED stays solid while held, and releasing fires the same
  capture/spray/record/upload flow as a real bird detection.

## Install

On the Pi:

```bash
sudo apt update
sudo apt install -y python3-venv ffmpeg swig liblgpio-dev

git clone <this repo> /home/pi/git/bird-away
cd /home/pi/git/bird-away
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env          # then edit: OPENROUTER_API_KEY, RTSP_URL
cp config.yaml.example config.yaml   # then edit GPIO pin, durations, etc.
```

`swig` and `liblgpio-dev` are needed so `pip` can build the `lgpio` wheel,
which gpiozero uses as its GPIO backend on Raspberry Pi OS Bookworm/Trixie.
Without it gpiozero falls back to an experimental native pin factory and
prints `PinFactoryFallback` warnings on startup.

The clone path above (`/home/pi/git/bird-away`) matches the paths baked into
`systemd/bird-away.service`. If you install elsewhere, edit `WorkingDirectory`,
`EnvironmentFile`, and `ExecStart` in that unit to match.

## Configure

`.env` (secrets, never committed):

- `OPENROUTER_API_KEY` — your OpenRouter API key (https://openrouter.ai). Routes
  to whichever model `detector_model` selects.
- `RTSP_URL` — full RTSP URL with credentials, e.g.
  `rtsp://user:pass@192.168.1.50:554/stream`.
- `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` — only required if `r2_enabled:
  true` in `config.yaml`. See [Remote publishing](#remote-publishing-cloudflare-r2).

`config.yaml` (tunables):

- `interval_seconds` — how often to sample (default `60`).
- `spray_duration` — relay-on time in seconds when a bird is seen (default `3`).
- `video_duration` — clip length in seconds saved per event (default `7`).
- `gpio_pin` — BCM pin number wired to the relay input (default `17`).
- `relay_active_high` — `true` if the relay closes on logic-high, `false` if
  active-low (most cheap relay modules are active-low — check yours).
- `capture_dir` — where images and clips are saved (default `./captures`).
- `detector_model` — OpenRouter model id; default `anthropic/claude-haiku-4.5`.
- `detector_base_url` — OpenAI-compatible base URL; default
  `https://openrouter.ai/api/v1`. Override to point at a different provider.
- `detector_prompt` — system prompt sent to the vision model. Use a YAML
  literal block (`|`) to write it across multiple lines. See
  [Tuning the prompt](#tuning-the-prompt) for what makes a good one.
- `daytime_only` — when `true` (default), only run detection between 07:00 and
  19:00 local time. Set `false` to run 24/7 (e.g. for testing or with an IR
  camera).
- `motion_enabled`, `motion_threshold`, `motion_downscale` — local frame-diff
  gate; only call the vision API when consecutive frames differ enough.
  Threshold is on a 0-255 mean per-pixel scale; `5.0` is a reasonable start.
- `status_led_enabled` / `status_led_pin` — drive a status LED on a separate
  GPIO pin (default `24`). Heartbeat-blinks 0.5s every 10s while the service
  is running, blinks 1.5s on each frame capture, and rapid-blinks 5 times on a
  positive bird detection. Set `status_led_enabled: false` if you don't have
  the LED wired.
- `trigger_button_enabled` / `trigger_button_pin` — manual sprinkler trigger
  via a momentary switch on a GPIO pin (default `23`). Wired between the pin
  and ground; uses the Pi's internal pull-up. While held, the status LED
  stays solid; releasing runs the same flow as a real bird detection. Set
  `trigger_button_enabled: false` if no switch is wired.
- `retention_days` — delete local `detection-*.jpg` / `event-*.mp4` files
  older than this many days. Sweep runs at startup and hourly thereafter.
  Default `7`. Set to `0` (or negative) to keep everything. R2 objects are
  unaffected — manage their lifecycle in the bucket settings.
- `log_level` — `INFO` or `DEBUG`.
- R2 publishing keys (`r2_enabled`, `r2_account_id`, `r2_bucket`,
  `r2_public_base_url`, `r2_key_prefix`) — see
  [Remote publishing](#remote-publishing-cloudflare-r2).

## Remote publishing (Cloudflare R2)

Optional. When `r2_enabled: true`, each detection's snapshot JPEG and event
MP4 are uploaded to a Cloudflare R2 bucket so you can view them from any
browser. R2 is S3-compatible with no egress fees and a 10 GB free tier — for
typical usage this stays free indefinitely with a 30-day lifecycle rule.

1. Cloudflare → R2 → create a bucket (e.g. `bird-away`). In the bucket's
   settings, enable the **R2.dev subdomain** and copy the
   `https://pub-<hash>.r2.dev` URL.
2. R2 → "Manage R2 API tokens" → **Create API token**, scope **Object Read &
   Write** to that bucket. Save the **Access Key ID** and **Secret Access
   Key** — they're shown once.
3. Append to `.env`:

   ```
   R2_ACCESS_KEY_ID=...
   R2_SECRET_ACCESS_KEY=...
   ```

4. Add to `config.yaml`:

   ```yaml
   r2_enabled: true
   r2_account_id: <hex string from R2 dashboard>
   r2_bucket: <bucket name>
   r2_public_base_url: https://pub-<hash>.r2.dev
   ```

5. Optional: in the R2 dashboard, add a lifecycle rule "delete objects older
   than 30 days" to keep storage under the free tier.

Each detection produces two objects under
`<r2_key_prefix>/YYYY-MM-DD/`: `detection-<ts>.jpg` and `event-<ts>.mp4`.
Default `r2_key_prefix` is `events`. Local copies in `captures/` are
unchanged — R2 is additive, not a replacement.

## Run by hand

```bash
.venv/bin/python -m src.main
```

Logs go to stdout. Output files appear under `captures/`.

## Hardware tests

Run these one at a time to verify each piece of the chain.

```bash
.venv/bin/python scripts/test_camera.py        # writes /tmp/bird-away-test.jpg
.venv/bin/python scripts/test_detector.py path/to/sample.jpg   # prints yes/no
.venv/bin/python scripts/test_sprinkler.py 1   # clicks relay for 1 second
.venv/bin/python scripts/test_status_led.py    # runs each LED pattern in sequence
.venv/bin/python scripts/test_trigger_button.py # press to light LED, release to log
```

## Comparing models

`scripts/test_models.py` runs a list of vision models against a single saved
frame so you can see how each one handles the same input. Useful when picking
`detector_model` or tuning `detector_prompt` against tricky frames (small
distant birds, glare on water, dawn light, etc.).

First-time setup — copy the example config (the actual file is gitignored
so your local tweaks stay out of the repo):

```bash
cp scripts/models_config.yaml.example scripts/models_config.yaml
```

Then run against any local image:

```bash
.venv/bin/python scripts/test_models.py captures/detection-20260426T131922Z.jpg
```

Each model gets two prompts: a yes/no classifier (the project's bird detector
by default) and a freeform description (asks the model to describe the scene,
animals, people, actions). Both prompts and the model list live in
`scripts/models_config.yaml` and can be overridden — pass `--config <path>`
to use an alternate file. The script prints the raw response from each model
along with the input resolution, elapsed time, and token usage so you can
compare quality, cost, and latency at a glance.

## Run as a service

```bash
sudo cp systemd/bird-away.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bird-away
journalctl -u bird-away -f
```

The unit runs as user `pi` in group `gpio`. Adjust `User=`, `Group=`, and the
paths in `bird-away.service` if your install location differs.

## Tuning

- **False positives** (sprays when no birds): tighten `detector_prompt` in
  `config.yaml` (see [Tuning the prompt](#tuning-the-prompt)), or switch
  `detector_model` to a stronger vision model (e.g.
  `anthropic/claude-sonnet-4.5`).
- **Birds aren't fazed**: increase `spray_duration`, or check that the spray
  pattern actually covers where they land.
- **Storage filling up**: `captures/` grows unbounded. Add a cron job or
  `tmpfiles.d` rule to prune files older than N days.

### Tuning the prompt

`detector_prompt` is the system message sent to the vision model on every
frame that passes the motion gate. The model receives the prompt plus a single
still image, and the parser in `src/detector.py` calls it a bird if the reply
starts with `yes` (case-insensitive).

A few rules of thumb:

- **Be specific about what counts.** "A bird" is ambiguous — does a duck on
  the deck count? Birds in flight? Reflections in the water? Most false
  positives and false negatives come from leaving these unstated. The default
  prompt explicitly covers "in, on, or near the pool (including birds in
  flight directly above it)" for that reason.
- **Keep it short.** Long prompts cost more per call and rarely improve
  accuracy. If you find yourself writing a paragraph, switch to a stronger
  `detector_model` instead.
- **Pin the output format.** End with something like "Output only the single
  word." Models occasionally drift to "Yes." or "Yes, I see…" — those still
  match `startswith("yes")`, but rambling answers like "I'm not sure…" parse
  as `no`.
- **Iterate against saved frames.** Every detection writes a still to
  `captures/`. Point `scripts/test_detector.py captures/<file>.jpg` at known
  bird and non-bird frames to sanity-check a prompt change before restarting
  the service.
- **Restart after editing.** `config.yaml` is read once at startup —
  `sudo systemctl restart bird-away` to pick up changes.

## Safety notes

- The Pi only switches the relay's input. The valve's power must come from a
  separate supply sized for the solenoid. Never wire mains AC to the Pi.
- Confirm the relay polarity (active-high vs active-low) before attaching the
  valve. A relay stuck closed will leave water flowing.
- The `Sprinkler` class drives the GPIO low on `__exit__` and on close, so a
  clean exit (Ctrl-C, SIGTERM) will shut the valve. systemd will also restart
  the service on failure, which forces a fresh GPIO init.

## Architecture

```
                  ┌──────────────────────────────────────────────────────┐
                  │             Raspberry Pi (bird-away.service)         │
                  └──────────────────────────────────────────────────────┘

   Configuration
   ─────────────
       .env (secrets)            config.yaml (tunables)
       ─────────────             ──────────────────────
       OPENROUTER_API_KEY        interval_seconds, gpio_pin, spray_duration,
       RTSP_URL                  detector_model, detector_prompt, motion_*,
       R2_ACCESS_KEY_ID  *       daytime_only, r2_*  (* if r2_enabled), …
       R2_SECRET_ACCESS_KEY  *
              │                          │
              └─────────────┬────────────┘
                            ▼
                    src/config.py  ──►  Config dataclass (passed to all modules)


   Per-iteration flow  (loops every cfg.interval_seconds in src/main.py)
   ─────────────────────────────────────────────────────────────────────

   src/camera.py runs a background thread (PyAV / libav) that holds one
   long-lived RTSP/TCP session and continuously decodes frames. The main
   loop pulls the most recent JPEG from RAM in O(1) — no per-iteration
   handshake.

       ┌──────────────┐   persistent
       │ RTSP camera  │◄───────────────►  src/camera.py
       │  (IP cam)    │   H.264 stream    Camera class:
       └──────────────┘                     • bg thread: av.open + decode
                                            • capture_frame() → latest JPEG
                                              │
                                              ▼
                                  daytime_only gate (07:00-19:00 local)
                                              │
                                  outside ◄───┴───► inside
                                     │                │
                                     ▼                ▼
                                   sleep      src/motion.py
                                              MotionDetector.check()
                                              (frame-diff gate, local)
                                                      │
                                  score < thresh ◄────┴────► score ≥ thresh
                                        │                          │
                                        ▼                          ▼
                                      sleep             src/detector.py
                                                        downscale → 512px
                                                        Detector.is_bird_present()
                                                                  │ HTTPS
                                                                  ▼
                                                    ┌──────────────────────┐
                                                    │   OpenRouter API     │
                                                    │ (Claude vision model │
                                                    │  per detector_model) │
                                                    └──────────┬───────────┘
                                                               │ "yes" / "no"
                                                               ▼
                                                    ┌──────────┴──────────┐
                                                    ▼  no                 ▼  yes
                                                  sleep             _handle_event()
                                                                          │
                              ┌───────────────────────────────────────────┤
                              ▼                                           ▼
                      pause bg thread,                        save still + spawn
                      spawn ffmpeg                            ffmpeg recorder
                      (separate RTSP                          (captures/event-*.mp4)
                       session, camera                                    │
                       only allows 1-2)                          ┌────────┴────────┐
                              │                                  ▼                 ▼
                              ▼                          src/sprinkler.py    src/uploader.py
                       resume bg thread                  GPIO via gpiozero   R2Uploader
                       after ffmpeg                      + lgpio             (boto3 → S3 API)
                                                                │                 │
                                                                ▼                 ▼
                                                          GPIO pin 17       Cloudflare R2
                                                                            (public bucket)

   Hardware chain (off the Pi)
   ───────────────────────────

       GPIO 17 ──► Relay module ──► 12V solenoid valve ──► sprinkler ──► pool surface
                   (active high/                ▲
                    low configurable)           │
                                          separate PSU
                                          (never from Pi)


   Background pieces
   ─────────────────
       systemd/bird-away.service  →  runs `python -m src.main` as user pi (group gpio),
                                     restarts on failure, logs to journal
       captures/                  →  unbounded; prune via cron / tmpfiles.d
       scripts/test_*.py          →  per-stage smoke tests (camera / detector / sprinkler)
```
