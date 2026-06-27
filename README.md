<img width="300" alt="image" src="https://github.com/user-attachments/assets/51d76a95-e8b6-47e1-b92c-d38c6e815195" />

A Raspberry Pi service that watches a pool through an RTSP camera, asks Claude
whether birds are present, and triggers a sprinkler (via relay + solenoid valve)
to startle them. Each detection saves a still image and a short video clip,
and can optionally publish both to a Cloudflare R2 bucket so they're viewable
from anywhere on the internet.

<img width="400" height="225" alt="event-20260622T192023Z" src="https://github.com/user-attachments/assets/2fb09415-a968-4a3c-9c0a-fbbbb147f2fd" />

## Hardware
<img width="1110" height="161" alt="image" src="https://github.com/user-attachments/assets/c858b5ad-f7e6-4a01-8866-c37de9c4c621" />

- Raspberry Pi 4 (or earlier) on Wi-Fi.
- Relay module wired to one Pi GPIO pin and ground. Active-high or active-low
  is configurable.
- RTSP-capable IP camera reachable on the same network.
- Solenoid valve on its own power supply, switched by the relay. The Pi must
  not source current to the valve directly.
- Sprinkler aimed to spray over and around the pool when the valve opens.
- optional - temperature/humidity sensor for monitoring ambient conditions in the enclosure
- Momentary switch (the contact pair on the same panel-mount
  button) for a manual sprinkler trigger. One contact → GPIO pin (default
  `23`), other → ground; the Pi's internal pull-up holds it HIGH at rest. The
  status LED stays solid while held, and releasing fires the same
  capture/spray/record/upload flow as a real bird detection.

<img width="250" alt="image" src="https://github.com/user-attachments/assets/63336784-0bd0-4567-83ed-71b2f77b96e4" /> <img width="200" alt="image" src="https://github.com/user-attachments/assets/fb4cb09c-7633-4d01-89f5-0a0e8c217455" /> <img width="300" alt="image" src="https://github.com/user-attachments/assets/7113ca67-c01e-44e7-818b-d237dc5282a3" />




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
- `pre_spray_seconds` — seconds of video recorded before the spray fires, so
  the clip captures the moment leading up to it (default `3`).
- `post_spray_seconds` — seconds of video recorded after the spray fires
  (default `4`). Total clip length is `pre_spray_seconds + spray_duration + post_spray_seconds`.
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
- `healthcheck_url` / `healthcheck_interval_seconds` — liveness ping to
  [healthchecks.io](https://healthchecks.io) (or any URL accepting a GET).
  Pinged after each successful loop iteration, rate-limited to once per
  interval (default `300s`). Pings stop if the loop hangs or throws every
  iteration, so healthchecks.io alerts you by email/SMS. Leave blank to
  disable. Recommended for unattended deployments.
- `log_level` — `INFO` or `DEBUG`.
- R2 publishing keys (`r2_enabled`, `r2_account_id`, `r2_bucket`,
  `r2_public_base_url`, `r2_key_prefix`) — see
  [Remote publishing](#remote-publishing-cloudflare-r2).
- `delete_after_upload` — when `true`, skip the local JPEG write entirely
  and delete each MP4 right after R2 confirms the upload. Requires
  `r2_enabled: true`. Pair with `capture_dir: /dev/shm/bird-away` for
  zero SD-card writes. See [Minimizing SD-card writes](#minimizing-sd-card-writes).

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
Default `r2_key_prefix` is `events`. By default R2 is additive — local
copies in `captures/` are also written and pruned by `retention_days`. To
make R2 the only copy and stop writing to the SD card, see
[Minimizing SD-card writes](#minimizing-sd-card-writes).

## Web dashboard
<img width="550" alt="image" src="https://github.com/user-attachments/assets/b501166a-2edc-4928-a1f7-470b0a64b1cd" />

When R2 publishing is enabled, the service also maintains a `manifest.json`
in the bucket (at `<r2_key_prefix>/manifest.json`). The manifest is a JSON
index of the last 500 detection events with public URLs for each snapshot and
video. `web/index.html` is a self-contained dashboard that reads this
manifest and renders an interactive event timeline.

### Deploying to Vercel

The dashboard is a static site — deploy the `web/` directory to Vercel:

```bash
cd web
npx vercel --prod
```

Set the `MANIFEST_URL` environment variable on the Vercel project so
visitors see data immediately without any manual configuration:

```bash
cd web
npx vercel env add MANIFEST_URL production
# paste: https://pub-<hash>.r2.dev/events/manifest.json
npx vercel --prod        # redeploy to pick up the new env var
```

Or set it in the Vercel dashboard under Project → Settings → Environment
Variables. The build step writes this value into `dashboard-config.json`,
which the dashboard loads automatically on startup.

Visitors can still override the URL via the settings modal or the
`?manifest=` query parameter.

### Other hosting options

The dashboard is a single `index.html` with no build step, so it works
anywhere: open it locally as a file, deploy to GitHub Pages or Netlify,
or upload it to the same R2 bucket as your events.

### Features

- **Live status**: shows connection state, last event time, event count.
- **Event grid**: thumbnail cards for each detection with date, time, and
  trigger type (auto / manual).
- **Filtering**: filter by date or trigger type.
- **Lightbox**: click any card to see the full snapshot; links to open the
  image or play the video.
- **Auto-refresh**: polls the manifest at a configurable interval (default
  60 seconds).

### CORS

If the dashboard is served from a different origin than R2 (e.g. localhost
or a Vercel deploy), the browser needs CORS headers on the manifest. In the
Cloudflare dashboard, go to your R2 bucket → Settings → CORS policy and add:

```json
[
  {
    "AllowedOrigins": ["*"],
    "AllowedMethods": ["GET"],
    "AllowedHeaders": ["*"]
  }
]
```

R2.dev public subdomains include permissive CORS headers by default, so this
is usually not needed unless you've customized the bucket's CORS settings.

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

## Minimizing SD-card writes

For unattended deployments, SD-card wear is the most common cause of Pi
death — every snapshot JPEG and event MP4 written to `captures/` consumes
write cycles. Cards typically tolerate 10k-100k writes per cell, and a busy
day at the pool can produce dozens of MB; over months that adds up.

Two settings together eliminate ~all capture-related SD writes:

```yaml
r2_enabled: true             # uploads still go to R2
delete_after_upload: true    # don't keep a local copy
capture_dir: /dev/shm/bird-away   # tmpfs (RAM); never touches the SD card
```

How it works:

- **JPEG snapshot.** With `delete_after_upload: true`, the bytes are
  uploaded straight from memory via `put_object` — `write_bytes` is never
  called and `image_path` never exists on disk.
- **Event MP4.** ffmpeg has to write to a path, so the file lives briefly
  in `capture_dir` while it records. With `capture_dir` pointed at
  `/dev/shm` (a kernel-managed tmpfs sized to half of RAM by default),
  that "file" lives in RAM. Once the R2 upload returns success, the file
  is deleted. If the upload fails, the file stays so `retention_days` can
  clean it up later.
- **Bonus (`/dev/shm`).** Faster than the SD card and survives nothing —
  a reboot wipes it. That's exactly what you want for transient capture
  data once R2 has the durable copy.

Tradeoff to know: with `delete_after_upload: true`, an extended R2 outage
loses captures (failed uploads stay on disk only until `retention_days`
expires, vs. the default behavior where they persist until you manually
copy them off). For a deterrent system this is usually the right trade —
SD-card death is permanent, a missed video clip is not.

If you want to keep the local copies (for debugging, or because you don't
have R2 set up), leave `delete_after_upload: false` (the default) and let
`retention_days` bound disk usage. You can still point `capture_dir` at
`/dev/shm/bird-away` to get RAM-backed storage with retention-based
cleanup — useful if you want a few days of local history without SD wear.

## Resilience for unattended deployments

For setups that need to run for months without intervention, the unit ships
with two watchdogs and `Restart=always`. The defaults Just Work, but two
extra one-time setup steps make recovery faster.

**Software watchdog (already wired up).** The service uses `Type=notify` and
the main loop pings `WATCHDOG=1` at the top of each iteration and once per
second while sleeping. If the loop hangs for more than `WatchdogSec=120`,
systemd kills and restarts the process. Combined with `Restart=always` this
also catches clean exits, OOM kills, segfaults — anything short of a kernel
hang.

**Hardware watchdog (one-time system config).** The Pi's BCM watchdog will
reset the SoC if the kernel itself hangs (rare but possible — bad SD card
sector, GPU lockup). On Pi 4/5 with current Pi OS, `/dev/watchdog` is
already exposed; you just need to tell systemd to use it:

```bash
sudo sed -i 's/^#RuntimeWatchdogSec=off/RuntimeWatchdogSec=15/' /etc/systemd/system.conf
sudo systemctl daemon-reexec
```

On older Pi OS (Bullseye and earlier) you may also need
`dtparam=watchdog=on` in `/boot/firmware/config.txt` (or `/boot/config.txt`
on pre-Bookworm) followed by a reboot. Check `ls /dev/watchdog` first — if
it exists, you're already set.

**Liveness alerting.** Set `healthcheck_url` in `config.yaml` to a free
[healthchecks.io](https://healthchecks.io) URL — see the config docs above.
This catches the failure mode where the loop is alive and pinging the
watchdog but every iteration is throwing (e.g. RTSP camera unreachable).

## Tuning

- **False positives** (sprays when no birds): tighten `detector_prompt` in
  `config.yaml` (see [Tuning the prompt](#tuning-the-prompt)), or switch
  `detector_model` to a stronger vision model (e.g.
  `anthropic/claude-sonnet-4.5`).
- **Birds aren't fazed**: increase `spray_duration`, or check that the spray
  pattern actually covers where they land.
- **Storage filling up**: tune `retention_days` (default `7`). The service
  prunes `captures/` at startup and hourly. R2 lifecycle rules handle the
  cloud copy.

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
