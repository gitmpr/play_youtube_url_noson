# Architecture and Development Decisions

This document explains the key design choices made in youtube-to-sonos and the
reasoning behind them. It is aimed at contributors and future maintainers, not
end users.

---

## Project structure

```
play_youtube_url_noson      Entry point — single video playback
queue_youtube_noson         Entry point — queue a video or playlist
yt_sonos.py                 Shared library for all common logic
install.sh                  One-shot setup: venv, symlinks, config template
config.example.json         Config template copied by install.sh
tests/
  integration_test.py       End-to-end test runner
  stubs/
    yt-dlp                  Realistic yt-dlp stub (generates real MP3 via ffmpeg)
    sonos                   soco-cli stub with stateful queue simulation
    sonos-discover          Discovery stub
Dockerfile                  Self-contained test environment (Ubuntu 24.04)
```

### Why two entry-point scripts instead of one with subcommands

The two use cases — play immediately vs. queue — have meaningfully different
argument sets and output behaviour. A single entry point with subcommands
(`cmd play`, `cmd queue`) would require users to remember the subcommand name,
which adds friction for the most common case. Two separate commands that do one
thing each follow the Unix tool philosophy and are easier to bind to a keyboard
shortcut or launcher.

Playlist URLs entered into `play_youtube_url_noson` are transparently delegated
to `queue_youtube_noson` via `os.execvp`, so users never have to think about
which command handles playlists.

---

## Shared library: yt_sonos.py

All shared logic lives in a single module rather than a package. The project is
small enough that a flat layout is easier to navigate than a `src/` tree, and it
avoids the import path complexity that packages introduce when scripts are run
directly.

The module is imported by both entry scripts. Because Python executes module-level
code at import time, `yt_sonos.py` loads the config and sets `MUSIC_DIR` and
`HTTP_PORT` as constants at the top. This means they are resolved once per
process, not on every call.

When `first_run_wizard()` creates a new config mid-run, it updates `MUSIC_DIR`
via a `global` declaration so the rest of that process uses the user's chosen
directory rather than the pre-import default.

---

## venv re-exec pattern

The entry scripts begin with:

```python
_venv_python = _here / ".venv/bin/python3"
if _venv_python.exists() and Path(sys.executable).resolve() != _venv_python.resolve():
    import os
    os.execv(str(_venv_python), [str(_venv_python)] + sys.argv)
```

When invoked with the system Python (e.g., because `~/.local/bin/play_youtube_url_noson`
is a symlink and the shell resolves the shebang against the system Python), this
re-execs the same script using the venv Python before any other imports happen.
The effect is that dependencies like tqdm and soco-cli are always available without
requiring the user to activate the venv manually.

`yt_sonos.py` additionally prepends `.venv/bin` to `PATH` so that subprocess
calls to `yt-dlp` and `sonos` also resolve to the venv-local copies rather than
any system-installed versions.

---

## Sonos control via soco-cli (subprocess), not the soco Python library

soco-cli provides the `sonos` and `sonos-discover` command-line tools. The
alternative would be to use the soco Python library directly (which soco-cli
wraps). The subprocess approach was chosen because:

- soco-cli handles speaker discovery, local cache (`~/.soco-cli/speakers_v2.pickle`),
  and UPnP details that would need to be reimplemented against the raw library.
- Subprocess calls are straightforward to stub in tests — the stubs replace the
  binaries in the venv and the test assertions inspect what commands were issued.
- soco-cli's speaker cache survives across invocations, which matters for
  performance when running the play command repeatedly.

The downside is that each Sonos operation is a subprocess invocation with its
own startup cost. For the use case (human-triggered playback), this is not
noticeable.

---

## HTTP server daemon for serving MP3 files to Sonos

Sonos cannot play files from the local filesystem — it requires an HTTP URL.
The project runs a lightweight HTTP server in a double-forked daemon process
that serves `MUSIC_DIR` on a configured port.

The double-fork pattern (POSIX daemon idiom) fully detaches the server from the
terminal session and ensures it survives after the play command exits. The server
is started lazily the first time it is needed and checked with a socket probe
before each use.

The Sonos device fetches the MP3 via `play_uri` or `add_uri_to_queue` using the
machine's non-loopback IP address. `get_local_ip()` resolves this by opening a
UDP socket toward 8.8.8.8 (no data is sent; this selects the routing source
address). If only a loopback address is found, a warning is printed to stderr
because Sonos will not be able to reach it.

Port is configurable (`http_port` in config) because multiple instances or
conflicting local services may use port 8000.

---

## yt-dlp format selection and fallback

The primary format selector is:

```
bestaudio[protocol^=https]/best[protocol^=https]/bestaudio
```

This prefers HTTPS sources over HLS (`m3u8`). YouTube's HLS streams require
fetching fragments sequentially via a playlist manifest, and the fragment URLs
expire quickly — they frequently return 403 errors by the time yt-dlp attempts
to download them. HTTPS sources download as a single request and are more
reliable.

If the preferred format fails, the fallback `bestaudio/best` is tried. This
covers edge cases where only HLS is available. The error from the first attempt
is preserved and printed if both fail, not the error from the fallback attempt
(which is typically less informative).

---

## Config file location and format

Config lives at `~/.config/youtube_to_sonos_config.json`, following the XDG
base directory convention for Linux and a familiar pattern on macOS.

It is explicitly not in the project directory. Keeping config out of the repo
prevents accidental commits of personal settings and means the same repo clone
can be shared across user accounts.

The config file is optional. All values have built-in defaults and the scripts
function without any config file. A malformed config JSON produces a stderr
warning and falls back to defaults rather than crashing.

---

## First-run wizard

When no config file exists and the terminal is interactive, the scripts run a
setup wizard before any other work. It:

1. Prompts for the music download directory using a pre-filled editable default
   (Python `readline.set_startup_hook` — equivalent to bash `read -e -i`).
2. Runs speaker discovery and presents a numbered list if multiple speakers exist.
3. Writes the config and prints the path where it was created.
4. Updates the module-level `MUSIC_DIR` so the current process immediately uses
   the chosen directory without needing a restart.

The wizard is intentionally skipped when `sys.stdin.isatty()` is False, which
covers piped invocations, cron jobs, and scripts that pass `-u URL`. Blocking on
input in non-interactive contexts would be a silent hang, not a usable prompt.

---

## yt-dlp auto-upgrade

yt-dlp is updated frequently to keep up with YouTube's format changes. The
scripts call `uv pip install --upgrade yt-dlp` at most once per 24 hours,
gated by a timestamp file in `MUSIC_DIR`. The upgrade runs against the venv
Python explicitly (`--python sys.executable`) rather than the ambient Python to
ensure it lands in the right venv.

The check is skipped if either `uv` or `yt-dlp` is not found, avoiding errors
in stripped environments.

---

## Dependency checking and uv bootstrapping

`check_dependencies()` runs at startup. If both `uv` and `yt-dlp` are absent,
it short-circuits with a single clear message pointing to the uv installer,
rather than listing every missing package separately. When only the Python deps
are missing but uv is present, it tells the user to run `install.sh`. This
covers the two most common first-run failure modes without confusing output.

The `shutil.which` calls find yt-dlp and sonos in the venv because `yt_sonos.py`
prepends the venv bin directory to `PATH` at import time. The dependency check
effectively verifies the venv is set up correctly, not just that the tools exist
somewhere on the system.

---

## Testing strategy

### Why integration tests over unit tests

The project's core behaviour is subprocess composition: it calls yt-dlp, calls
soco-cli commands in the right order, and serves files to Sonos. Unit tests
mocking these subprocess calls would verify that the correct strings are
assembled, but not that the end-to-end flow works.

Integration tests run the actual entry-point scripts against realistic stubs,
verifying that the correct external commands are issued and that state changes
occur as expected.

### Realistic stubs, not mocks

The stubs are standalone scripts placed in `.venv/bin/` so they are found first
on PATH. They maintain state across subprocess calls using files in `/tmp/`:

- `sonos` stub persists a queue in `/tmp/sonos_stub_state.json` — multiple
  `sonos add_uri_to_queue` calls accumulate into the same queue, exactly as the
  real soco-cli would behave.
- `yt-dlp` stub generates a real two-second silent MP3 via ffmpeg for download
  invocations, so the HTTP server can actually serve it and the file-existence
  checks in the main code pass.

This means the tests exercise the full code path including file I/O, HTTP server
startup, and multi-process coordination.

### Docker as the test environment

Tests run inside a Docker container based on Ubuntu 24.04, which matches the
GitHub Actions ubuntu-latest runner image. This means:

- Tests run in a clean environment with no host state bleeding in.
- The exact same image can be used for CI without environment divergence.
- uv, ffmpeg, and Python are installed in the container, so the test exercises
  the real install path, not a pre-configured developer machine.

The Dockerfile runs `install.sh` as part of the image build, then replaces the
venv binaries with stubs. This sequence verifies that `install.sh` works
correctly on a clean system as a side effect of building the test image.

### Test stub extensibility

Stubs read environment variables to vary their behaviour. For example,
`YTDLP_STUB_FAIL_TRACK=1` causes the stub to fail on the first download
invocation. This allows testing failure-handling paths (e.g., verifying that
the Sonos queue is cleared even when the first playlist track fails to download)
without needing multiple stub implementations.

---

## URL input design

The URL resolution order is: explicit `-u` argument → clipboard → interactive
prompt. This enables three common workflows without any flags:

1. Copy a YouTube URL, run the command — clipboard is read automatically.
2. Run the command, paste a URL when prompted — for terminals without clipboard
   integration.
3. Pass `-u URL` for scripted or shortcut-triggered invocations.

All three paths go through `normalize_url()`, which prepends `https://` to bare
URLs (without a scheme). This handles the common habit of copying from a browser
address bar that omits the protocol.

Non-YouTube URLs are rejected early with a specific error message rather than
passed to yt-dlp, which would produce a cryptic format-selection error.
