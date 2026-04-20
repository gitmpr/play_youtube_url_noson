FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 curl ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv and make it globally available
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && ln -sf /root/.local/bin/uv /usr/local/bin/uv

WORKDIR /app

# Copy project files
COPY . .

# Run install.sh — creates .venv and installs tqdm, yt-dlp, soco-cli via uv sync
RUN bash install.sh

# Replace venv binaries with test stubs.
# yt_sonos.py prepends .venv/bin to PATH, so stubs must live here to take priority.
RUN chmod +x tests/stubs/yt-dlp tests/stubs/sonos tests/stubs/sonos-discover \
    && cp tests/stubs/yt-dlp      .venv/bin/yt-dlp \
    && cp tests/stubs/sonos       .venv/bin/sonos \
    && cp tests/stubs/sonos-discover .venv/bin/sonos-discover

# Create test config (port offset from 8000 to avoid conflicts)
RUN mkdir -p /root/.config /tmp/music \
    && printf '{"default_speaker": "Living Room", "music_dir": "/tmp/music", "http_port": 8765}\n' \
       > /root/.config/youtube_to_sonos_config.json

# Pre-touch the yt-dlp upgrade check file so the auto-upgrade is skipped
RUN touch /tmp/music/.ytdlp_last_upgrade_check

# Symlinks expected by the scripts (install.sh targets ~/.local/bin which is in PATH)
ENV PATH="/root/.local/bin:/app/.venv/bin:$PATH"

CMD ["python3", "tests/integration_test.py"]
