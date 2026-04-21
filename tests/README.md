# Tests

Two testing modes are available: stub-based integration tests that run
anywhere without a Sonos system, and live tests against a real speaker over
the local network.

---

## Stub-based integration tests

`integration_test.py` runs the CLI scripts end-to-end against realistic
stub binaries that replace `yt-dlp`, `sonos`, and `sonos-discover` inside
the project venv. The stubs maintain state across subprocess calls using
files in `/tmp/`, so the tests verify the full call sequence rather than
just mocking individual functions.

The `yt-dlp` stub generates a real two-second silent MP3 via ffmpeg, so
file-existence checks and HTTP server behaviour are exercised for real.
The `sonos` stub keeps a live queue in `/tmp/sonos_stub_state.json`, which
means `add_uri_to_queue`, `queue_length`, `play_from_queue` etc. all
interact with shared state just as the real soco-cli would.

### Run in Docker (recommended)

`Dockerfile` builds an Ubuntu 24.04 image, runs `install.sh`, then
replaces the venv binaries with the stubs before executing the test runner.

```bash
# Build
docker build -t yt-sonos-test .

# Run
docker run --rm yt-sonos-test
```

### Run directly on the host

The stubs must be on PATH ahead of the real binaries. The simplest way is
to copy them into the project venv, which `yt_sonos.py` prepends to PATH
at startup:

```bash
cp tests/stubs/yt-dlp      .venv/bin/yt-dlp
cp tests/stubs/sonos       .venv/bin/sonos
cp tests/stubs/sonos-discover .venv/bin/sonos-discover
chmod +x .venv/bin/yt-dlp .venv/bin/sonos .venv/bin/sonos-discover

.venv/bin/python3 tests/integration_test.py
```

Restore the real binaries afterwards with `uv sync --reinstall`.

### Stubs

| Stub | Location | What it does |
|------|----------|--------------|
| `yt-dlp` | `tests/stubs/yt-dlp` | Returns a fixed title/duration for `--get-title`, fake playlist entries for `--flat-playlist`, generates a real 2-second silent MP3 via ffmpeg for `--extract-audio`. Supports `YTDLP_STUB_FAIL_TRACK=N` to simulate a download failure on track N. |
| `sonos` | `tests/stubs/sonos` | Handles stop, clear_queue, play_uri, add_uri_to_queue, queue_length, queue_position, play_from_queue, next. Persists queue state to `/tmp/sonos_stub_state.json`. Knows two speakers: `Living Room` and `Kitchen`. |
| `sonos-discover` | `tests/stubs/sonos-discover` | Prints the two known speakers and exits. |

---

## Live tests against a real Sonos speaker

`Dockerfile.live` builds the project with real binaries — no stubs. The
container is run with `--network host` so it shares the host's network
interface. This is required because:

- Sonos speaker discovery uses UPnP multicast, which does not cross Docker's
  default NAT bridge.
- The Sonos device fetches MP3s over HTTP from the container's IP; with host
  networking that IP is the host machine's LAN address, which Sonos can reach.

macvlan networking (giving the container its own DHCP address) is an
alternative on wired ethernet, but is not supported on WiFi because most
access points only forward frames for the MAC address they authenticated.
`--network host` works on both.

### Build

```bash
docker build -f Dockerfile.live -t yt-sonos-live .
```

### Run interactively

```bash
docker run --rm -it \
  --network host \
  -v "${HOME}/.config/youtube_to_sonos_config.json:/root/.config/youtube_to_sonos_config.json:ro" \
  -v "${HOME}/Music/MP3:${HOME}/Music/MP3" \
  yt-sonos-live
```

This drops you into a bash shell. From there:

```bash
play_youtube_url_noson --list-speakers
play_youtube_url_noson -u 'https://www.youtube.com/watch?v=...'
queue_youtube_noson -u 'https://www.youtube.com/playlist?list=...' --shuffle
```

### Run a single command directly

```bash
docker run --rm \
  --network host \
  -v "${HOME}/.config/youtube_to_sonos_config.json:/root/.config/youtube_to_sonos_config.json:ro" \
  -v "${HOME}/Music/MP3:${HOME}/Music/MP3" \
  yt-sonos-live \
  play_youtube_url_noson -u 'https://www.youtube.com/watch?v=...'
```

### Via docker compose

`docker-compose.yml` in the project root defines both services with the
correct volume mounts pre-configured.

```bash
# Stub integration tests
docker compose run --rm test

# Live interactive shell
docker compose run --rm live

# Live single command
docker compose run --rm live play_youtube_url_noson -u 'https://...'
```

### Volume mounts

| Host path | Container path | Purpose |
|-----------|---------------|---------|
| `~/.config/youtube_to_sonos_config.json` | `/root/.config/youtube_to_sonos_config.json` | Speaker name, music dir, HTTP port |
| `~/Music/MP3` (or whatever `music_dir` is set to) | same absolute path | Downloaded MP3s shared with host, persist across runs |

The music dir is mounted at the same absolute path it has on the host so the
config file works inside the container without modification.
