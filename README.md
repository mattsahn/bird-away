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
sudo apt install -y python3-venv ffmpeg

git clone <this repo> /home/pi/bird-away
cd /home/pi/bird-away
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env          # then edit: OPENROUTER_API_KEY, RTSP_URL
cp config.yaml.example config.yaml   # then edit GPIO pin, durations, etc.
```

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

- **False positives** (sprays when no birds): tighten the system prompt in
  `src/detector.py`, or switch `detector_model` to a stronger vision model
  (e.g. `anthropic/claude-sonnet-4.5`).
- **Birds aren't fazed**: increase `spray_duration`, or check that the spray
  pattern actually covers where they land.
- **Storage filling up**: `captures/` grows unbounded. Add a cron job or
  `tmpfiles.d` rule to prune files older than N days.

## Safety notes

- The Pi only switches the relay's input. The valve's power must come from a
  separate supply sized for the solenoid. Never wire mains AC to the Pi.
- Confirm the relay polarity (active-high vs active-low) before attaching the
  valve. A relay stuck closed will leave water flowing.
- The `Sprinkler` class drives the GPIO low on `__exit__` and on close, so a
  clean exit (Ctrl-C, SIGTERM) will shut the valve. systemd will also restart
  the service on failure, which forces a fresh GPIO init.
