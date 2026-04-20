# youtube-to-sonos

Play YouTube videos and playlists on Sonos speakers. Downloads audio as MP3,
serves it over a local HTTP server, and controls Sonos playback via soco-cli.

## Requirements

- Python 3.8+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- ffmpeg (system package — for MP3 conversion)
- A Sonos system on the same local network

## Installation

```bash
git clone https://github.com/yourusername/youtube-to-sonos
cd youtube-to-sonos
./install.sh
```

`install.sh` will:

1. Create a virtual environment and install all Python dependencies (tqdm, yt-dlp, soco-cli) via `uv sync`
2. Create symlinks to `play_youtube_url_noson` and `queue_youtube_noson` in `~/.local/bin/`
3. Copy `config.example.json` to `~/.config/youtube_to_sonos_config.json` on first run
4. Check that ffmpeg is installed

To install uv if you don't have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

To install ffmpeg:

```bash
sudo apt install ffmpeg        # Ubuntu/Debian
brew install ffmpeg            # macOS
```

---

## Configuration

Edit `~/.config/youtube_to_sonos_config.json`:

```json
{
  "default_speaker": "Living Room",
  "music_dir": "~/Music/MP3",
  "http_port": 8000
}
```

- `default_speaker` — Room name as shown by `sonos-discover`. Saved automatically when you first use the script without `-s`.
- `music_dir` — Where MP3 files are downloaded and served from. Created on first run if it does not exist.
- `http_port` — Port the local HTTP server listens on. Sonos fetches MP3s from it.

The config file is created from `config.example.json` on first install. If it does not exist, built-in defaults are used.

---

## Commands

### play_youtube_url_noson

Play a single YouTube video on a Sonos speaker. Playlist URLs are automatically
delegated to `queue_youtube_noson`.

```
play_youtube_url_noson [-u URL] [-s SPEAKER] [--list-speakers] [--check-deps]
```

- `-u URL` — YouTube video or playlist URL. If omitted: reads clipboard, then prompts for paste.
- `-s SPEAKER` — Room name. Uses `default_speaker` from config if omitted; auto-discovers on first run.
- `--list-speakers` — Print available speakers and exit.
- `--check-deps` — Verify all dependencies and exit.

**URL input:**

URLs work with or without `https://`:

```bash
play_youtube_url_noson -u 'https://www.youtube.com/watch?v=...'
play_youtube_url_noson -u 'youtube.com/watch?v=...'
```

If `-u` is not provided, the script reads the clipboard. If no YouTube URL is found there,
it prompts you to paste one (on interactive terminals).

**What it does:**

1. Resolves URL (clipboard / stdin fallback) and speaker (config default / auto-discover).
2. Checks for a yt-dlp upgrade via uv (at most once per 24 hours).
3. Ensures the background HTTP server is running on the configured port.
4. Downloads the MP3 with a tqdm progress bar in the terminal.
5. Stops current Sonos playback and plays the new file immediately.
6. Returns the terminal. The HTTP server keeps serving in the background.

**Examples:**

```bash
# Play whatever YouTube URL is in the clipboard on the default speaker
play_youtube_url_noson

# Play a specific video on a specific speaker
play_youtube_url_noson -u 'https://www.youtube.com/watch?v=...' -s 'Living Room'

# List all available speakers
play_youtube_url_noson --list-speakers

# Check that all dependencies are installed
play_youtube_url_noson --check-deps
```

---

### queue_youtube_noson

Download and queue YouTube videos or full playlists on a Sonos speaker.
Shows a tqdm progress bar for each download. Starts playback as soon as
the first track is ready, then queues the rest while they download.

```
queue_youtube_noson [-u URL] [-s SPEAKER] [--shuffle] [--limit N] [--play-next] [-q]
```

- `-u URL` — YouTube video or playlist URL. If omitted: reads clipboard, then prompts for paste.
- `-s SPEAKER` — Room name. Uses `default_speaker` from config if omitted; auto-discovers on first run.
- `--shuffle` — Randomise playlist order before queuing.
- `--limit N` — Only queue the first N tracks from a playlist (default: 0 = no limit).
- `--play-next` — Insert the video as the next track rather than appending.
- `-q` / `--quiet` — Suppress output (useful in scripts).

**What it does for playlists:**

1. Fetches all playlist entries via yt-dlp.
2. Stops current playback and clears the Sonos queue.
3. Downloads track 1 with a progress bar.
4. Switches Sonos to queue mode and starts playing track 1.
5. Downloads tracks 2..N one at a time, each with a progress bar, adding
   each to the queue as it finishes. Sonos auto-advances through them.

Already-cached MP3s are added to the queue instantly with no download needed.

**Examples:**

```bash
# Queue a full playlist on the default speaker
queue_youtube_noson -u 'https://www.youtube.com/playlist?list=...'

# Shuffle and limit to 10 tracks
queue_youtube_noson -u 'PLAYLIST_URL' --shuffle --limit 10

# Add a single video as the next track in the current queue
queue_youtube_noson -u 'https://www.youtube.com/watch?v=...' --play-next

# Append a single video to the end of the current queue
queue_youtube_noson -u 'https://www.youtube.com/watch?v=...'
```

---

## First run

On first run with no speaker configured, the script discovers your Sonos network
automatically. If multiple speakers are found, you will be prompted to pick one
(saved as `default_speaker` for future runs).

If `music_dir` does not exist yet, the script offers to create it before downloading.

---

## Behaviour by scenario

| Scenario | What happens |
|----------|-------------|
| Play single track | Stops current playback, plays new track immediately via `play_uri` |
| Play playlist | Stops playback, clears queue, downloads track 1, calls `play_from_queue`, queues rest |
| Append to active queue | Adds to end of queue without interrupting playback |
| Insert as next track | `--play-next`: inserted after current track, skips to it |
| Ctrl+C mid-playlist | Already-queued tracks keep playing, remaining downloads are skipped |
| Cached tracks | Detected by filename, added instantly with no download |
| No URL provided | Reads clipboard; prompts stdin on interactive terminals |
| No speaker configured | Auto-discovers; prompts to pick if multiple found; saves choice |
| Music dir missing | Prompts to create it; error in quiet/non-interactive mode |

---

## Architecture

```
play_youtube_url_noson          queue_youtube_noson
        |                               |
        +-------- yt_sonos.py ----------+
                  (shared library)
                       |
        +--------------+------------------+
        |                                 |
  yt-dlp (download)            soco-cli (Sonos control)
        |                                 |
   ~/Music/MP3/               sonos play_uri / add_uri_to_queue
        |
  HTTP server daemon
  (serves MP3s to Sonos)
```

**HTTP server:** A forked daemon serving `music_dir` on `http_port`. Started
automatically if not already running. Survives across multiple play/queue invocations.

**yt-dlp format selection:** Prefers HTTPS formats over HLS to avoid 403
errors on HLS fragment downloads. Falls back to `bestaudio/best` if the
preferred format fails.

**yt-dlp auto-upgrade:** At most once per 24 hours, upgrades yt-dlp in the
project venv via `uv pip install --upgrade`. The timestamp is stored in
`{music_dir}/.ytdlp_last_upgrade_check`.

**Speaker cache:** `sonos-discover` is run before every playback command to
refresh the soco-cli speaker cache. This prevents failures when speaker IPs
change due to DHCP reassignment.

---

## Dependencies

All Python dependencies are managed by uv and installed into a project-local virtualenv by `install.sh`.

| Dependency | Type | Managed by |
|-----------|------|-----------|
| tqdm | Python package | uv (pyproject.toml) |
| yt-dlp | Python package | uv (pyproject.toml) |
| soco-cli | Python package | uv (pyproject.toml) |
| ffmpeg | System binary | apt / brew |
| uv | Package manager | manual (see above) |

Optional clipboard integration requires one of: `wl-paste` (Wayland), `xclip` (X11), or `pbpaste` (macOS).
Without clipboard support, the script prompts for a URL on interactive terminals and requires `-u URL` in scripts.

---

## Troubleshooting

**Dependency check**

```bash
play_youtube_url_noson --check-deps
```

**Speaker not found / wrong IP**

Run `sonos-discover` to refresh the cache, then retry. If the problem persists,
delete `~/.soco-cli/speakers_v2.pickle` to force a full rediscovery.

**yt-dlp download fails with 403**

The script retries automatically with a fallback format. If both fail, the
video may have geo or age restrictions. The error message will include a reason
from yt-dlp. Manually upgrade yt-dlp:

```bash
cd /path/to/youtube-to-sonos && uv sync --upgrade-package yt-dlp
```

**HTTP server returns 404**

A stale HTTP server process may be running from a different directory. Kill it
and re-run the play command:

```bash
kill $(ss -tlnp | awk '/:8000/{match($0,/pid=([0-9]+)/,a); print a[1]}')
```

Replace `8000` with your configured `http_port` if different.

**Config file warnings**

If the config file is malformed JSON, the script warns on stderr and uses
built-in defaults. Fix or delete `~/.config/youtube_to_sonos_config.json`
to clear the warning.

---

## File locations

| Path | Purpose |
|------|---------|
| `~/.local/bin/play_youtube_url_noson` | Symlink to script |
| `~/.local/bin/queue_youtube_noson` | Symlink to script |
| `~/.config/youtube_to_sonos_config.json` | Configuration |
| `{music_dir}/` | Downloaded MP3 files |
| `{music_dir}/.ytdlp_last_upgrade_check` | yt-dlp upgrade check timestamp |
| `~/.soco-cli/speakers_v2.pickle` | soco-cli speaker cache |
