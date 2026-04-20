#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
CONFIG_FILE="${HOME}/.config/youtube_to_sonos_config.json"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[ok]${NC}    $*"; }
warn() { echo -e "${YELLOW}[warn]${NC}  $*"; }
fail() { echo -e "${RED}[missing]${NC} $*"; }

echo "youtube-to-sonos installer"
echo "=========================="

# --- uv is required ---
if ! command -v uv &>/dev/null; then
    echo "uv is required but not found."
    echo "Install it with:"
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi
ok "uv ($(uv --version))"

# --- Install all Python deps into venv via uv ---
cd "$REPO_DIR"
uv sync --quiet
ok "Virtual environment ready (tqdm, yt-dlp, soco-cli installed)"

# --- Make scripts executable ---
chmod +x "${REPO_DIR}/play_youtube_url_noson"
chmod +x "${REPO_DIR}/queue_youtube_noson"

# --- Symlinks in ~/.local/bin ---
mkdir -p "$BIN_DIR"
for script in play_youtube_url_noson queue_youtube_noson; do
    target="${REPO_DIR}/${script}"
    link="${BIN_DIR}/${script}"
    if [[ -L "$link" && "$(readlink "$link")" == "$target" ]]; then
        ok "~/.local/bin/${script} (already linked)"
    else
        ln -sf "$target" "$link"
        ok "~/.local/bin/${script} -> ${target}"
    fi
done

# --- Config file ---
if [[ ! -f "$CONFIG_FILE" ]]; then
    mkdir -p "$(dirname "$CONFIG_FILE")"
    cp "${REPO_DIR}/config.example.json" "$CONFIG_FILE"
    warn "Created ${CONFIG_FILE} from template -- edit it to set your speaker name"
else
    ok "${CONFIG_FILE}"
fi

# --- Check ffmpeg (system package, not installable via uv) ---
echo ""
echo "System dependencies:"

ffmpeg_ok=0
if command -v ffmpeg &>/dev/null; then
    ok "ffmpeg"
    ffmpeg_ok=1
else
    fail "ffmpeg (required for MP3 conversion)"
    echo "         Ubuntu/Debian: sudo apt install ffmpeg"
    echo "         macOS:         brew install ffmpeg"
fi

# --- Check clipboard tool (optional) ---
clipboard_ok=0
for tool in wl-paste xclip pbpaste; do
    if command -v "$tool" &>/dev/null; then
        ok "clipboard ($tool)"
        clipboard_ok=1
        break
    fi
done
if [[ $clipboard_ok -eq 0 ]]; then
    warn "No clipboard tool found (URL-from-clipboard won't work)"
    echo "         Wayland: sudo apt install wl-clipboard"
    echo "         X11:     sudo apt install xclip"
fi

# --- Summary ---
echo ""
if [[ $ffmpeg_ok -eq 0 ]]; then
    echo -e "${YELLOW}Install ffmpeg above to complete setup.${NC}"
else
    echo -e "${GREEN}Setup complete.${NC}"
    echo ""
    echo "Next steps:"
    echo "  1. Edit ${CONFIG_FILE} to set your speaker name"
    echo "  2. Run: sonos-discover   (to see available room names)"
    echo "  3. Run: play_youtube_url_noson --list-speakers"
    echo "  4. Run: play_youtube_url_noson --help"
fi
