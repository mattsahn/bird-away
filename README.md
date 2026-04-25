# bird-away

A Raspberry Pi service that watches a pool through an RTSP camera, asks Claude
whether birds are present, and triggers a sprinkler (via relay + solenoid valve)
to startle them. Each detection saves a still image and a 30-second video clip
for review.

## Hardware

- Raspberry Pi 4 (or earlier) on Wi-Fi.
- RTSP-capable IP camera reachable on the same network.
- Relay module wired to one Pi GPIO pin and ground. Active-high or active-low
  is configurable.
- Solenoid valve on its own power supply, switched by the relay. The Pi must
  not source current to the valve directly.
- Sprinkler aimed to spray over and around the pool when the valve opens.

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

`config.yaml` (tunables):

- `interval_seconds` — how often to sample (default `60`).
- `spray_duration` — relay-on time in seconds when a bird is seen (default `3`).
- `video_duration` — clip length in seconds saved per event (default `30`).
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
- `log_level` — `INFO` or `DEBUG`.

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
```

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
       .env (secrets)         config.yaml (tunables)
       ─────────────          ──────────────────────
       OPENROUTER_API_KEY     interval_seconds, gpio_pin, spray_duration,
       RTSP_URL               detector_model, detector_prompt, motion_*, …
              │                       │
              └─────────┬─────────────┘
                        ▼
                  src/config.py  ──►  Config dataclass (passed to all modules)


   Per-iteration flow  (loops every cfg.interval_seconds in src/main.py)
   ─────────────────────────────────────────────────────────────────────

       ┌──────────────┐   JPEG bytes
       │ RTSP camera  │ ─────────────►  src/camera.py
       │  (IP cam)    │                 capture_frame()
       └──────────────┘                       │
                                              ▼
                                    src/motion.py
                                    MotionDetector.check()
                                    (frame-diff gate, local)
                                              │
                          score < thresh ◄────┴────► score ≥ thresh
                                │                          │
                                ▼                          ▼
                              sleep             src/detector.py
                                                Detector.is_bird_present()
                                                          │
                                                          │  HTTPS
                                                          ▼
                                            ┌──────────────────────┐
                                            │   OpenRouter API     │
                                            │ (Claude vision model │
                                            │  per detector_model) │
                                            └──────────┬───────────┘
                                                       │ "yes" / "no"
                                                       ▼
                                            ┌──────────┴──────────┐
                                            │                     │
                                            ▼  no                 ▼  yes
                                          sleep             _handle_event()
                                                                  │
                                              ┌───────────────────┼──────────────────┐
                                              ▼                   ▼                  ▼
                                        save still         start ffmpeg       sprinkler.fire()
                                        captures/          captures/          src/sprinkler.py
                                        detection-*.jpg    event-*.mp4               │
                                                           (30s clip)                │
                                                                                     ▼
                                                                            gpiozero + lgpio
                                                                                     │
                                                                                     ▼
                                                                              GPIO pin 17

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
