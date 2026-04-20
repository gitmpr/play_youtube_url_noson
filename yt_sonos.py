"""
Shared utilities for play_youtube_url_noson and queue_youtube_noson.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from socketserver import TCPServer
from urllib.parse import parse_qs, quote, urlparse

# When running inside the project venv, add its bin dir to PATH so that
# yt-dlp and sonos (installed as venv packages) are found by subprocess calls.
_venv_bin = Path(sys.executable).parent
if (_venv_bin / "yt-dlp").exists():
    os.environ["PATH"] = str(_venv_bin) + os.pathsep + os.environ.get("PATH", "")

CONFIG_FILE = Path("~/.config/youtube_to_sonos_config.json").expanduser()
YTDLP_FORMAT = "bestaudio[protocol^=https]/best[protocol^=https]/bestaudio"
YTDLP_FORMAT_FALLBACK = "bestaudio/best"

_YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"
}


def load_config():
    config = {}
    if CONFIG_FILE.exists():
        try:
            config = json.loads(CONFIG_FILE.read_text())
        except Exception as e:
            print(
                f"Warning: could not parse config file {CONFIG_FILE}: {e}",
                file=sys.stderr,
            )
            print(
                f"Using built-in defaults. Fix or delete {CONFIG_FILE} to clear this warning.",
                file=sys.stderr,
            )

    try:
        music_dir = Path(config.get("music_dir", "~/Music/MP3")).expanduser()
    except Exception:
        music_dir = Path("~/Music/MP3").expanduser()

    try:
        http_port = int(config.get("http_port", 8000))
    except (ValueError, TypeError):
        print(
            f"Warning: invalid 'http_port' in {CONFIG_FILE}, using default 8000",
            file=sys.stderr,
        )
        http_port = 8000

    return music_dir, http_port


MUSIC_DIR, HTTP_PORT = load_config()


def get_music_dir() -> Path:
    """Return the current MUSIC_DIR (may be updated by first_run_wizard)."""
    return MUSIC_DIR


# --- Dependency checking ---

def ensure_music_dir(quiet=False) -> bool:
    """Verify MUSIC_DIR exists and is a directory.

    If missing: prompt to create (interactive) or print an error (quiet/non-tty).
    Returns True if the directory is ready, False if the caller should abort.
    """
    if MUSIC_DIR.exists():
        if not MUSIC_DIR.is_dir():
            print(f"Error: 'music_dir' path exists but is not a directory: {MUSIC_DIR}")
            print(f"Update 'music_dir' in {CONFIG_FILE} to point to a directory.")
            return False
        return True

    if quiet or not sys.stdin.isatty():
        print(f"Music directory does not exist: {MUSIC_DIR}")
        print(f"Create it manually or update 'music_dir' in {CONFIG_FILE}.")
        return False

    print(f"Music directory does not exist: {MUSIC_DIR}")
    print(f"(Configured via 'music_dir' in {CONFIG_FILE})")
    try:
        answer = input("Create it now? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False

    if answer in ("", "y", "yes"):
        try:
            MUSIC_DIR.mkdir(parents=True, exist_ok=True)
            print(f"Created {MUSIC_DIR}")
            return True
        except OSError as e:
            print(f"Failed to create {MUSIC_DIR}: {e}")
            return False

    print(f"Aborted. Update 'music_dir' in {CONFIG_FILE} to a writable path.")
    return False


def check_dependencies():
    """Return a list of human-readable error strings for any missing dependencies."""
    issues = []

    # uv is the prerequisite for everything else. If it's missing and the Python
    # packages aren't installed either, guide the user to install uv first.
    if not shutil.which("uv") and not shutil.which("yt-dlp"):
        issues.append(
            "uv not found — it is required to install this project's dependencies.\n"
            "\n"
            "  Install uv (takes ~30 seconds):\n"
            "    curl -LsSf https://astral.sh/uv/install.sh | sh\n"
            "    source ~/.bashrc   # or open a new terminal\n"
            "\n"
            "  Then set up the project:\n"
            "    ./install.sh"
        )
        return issues  # All other checks will fail too — no point listing them

    if not shutil.which("ffmpeg"):
        issues.append(
            "ffmpeg not found (required for MP3 conversion)\n"
            "  Ubuntu/Debian: sudo apt install ffmpeg\n"
            "  macOS:         brew install ffmpeg"
        )

    if not shutil.which("yt-dlp"):
        issues.append(
            "yt-dlp not found\n"
            "  Run: ./install.sh  (installs all Python deps via uv)"
        )

    if not shutil.which("sonos"):
        issues.append(
            "sonos (soco-cli) not found\n"
            "  Run: ./install.sh  (installs all Python deps via uv)"
        )

    import importlib.util
    if importlib.util.find_spec("tqdm") is None:
        issues.append(
            "tqdm Python package not found\n"
            "  Run: ./install.sh  (installs all Python deps via uv)"
        )

    return issues


# --- First-run wizard ---

def _readline_input(prompt, prefill=""):
    """input() with a pre-filled, editable default value (like bash read -e -i)."""
    try:
        import readline
        readline.set_startup_hook(lambda: readline.insert_text(prefill))
        try:
            return input(prompt)
        finally:
            readline.set_startup_hook()
    except ImportError:
        return input(f"{prompt}[{prefill}] ") if prefill else input(prompt)


def first_run_wizard():
    """Guide a first-time user through initial configuration.

    Runs only when no config file exists and the terminal is interactive.
    Writes ~/.config/youtube_to_sonos_config.json and updates the module-level
    MUSIC_DIR so the rest of the current process uses the chosen values.
    """
    global MUSIC_DIR  # noqa: PLW0603

    if CONFIG_FILE.exists():
        return
    if not sys.stdin.isatty():
        return

    sep = "=" * 52
    print(sep)
    print("First-time setup — youtube-to-sonos")
    print(sep)
    print(f"No config file found at {CONFIG_FILE}")
    print("Let's set up the basics.\n")

    # Music directory — pre-filled so user can edit the path directly
    default_music = str(Path.home() / "Music" / "MP3")
    try:
        raw = _readline_input("Music download directory: ", default_music).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    music_dir = Path(raw).expanduser() if raw else Path(default_music)

    # Discover speakers
    print("\nDiscovering Sonos speakers on your network...")
    speakers = discover_sonos_speakers()

    default_speaker = None
    if not speakers:
        print("No speakers found.")
        print("Make sure your Sonos system is on and reachable on the same network.")
        print("You can set a default speaker later with -s SPEAKER or by editing the config.\n")
    elif len(speakers) == 1:
        default_speaker = speakers[0]
        print(f"Found one speaker: {default_speaker}\n")
    else:
        print(f"Found {len(speakers)} speakers:")
        for i, s in enumerate(speakers, 1):
            print(f"  {i}. {s}")
        try:
            choice = input(
                f"Select default [1-{len(speakers)}] or Enter for '{speakers[0]}': "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            choice = ""
        default_speaker = (
            speakers[int(choice) - 1]
            if choice.isdigit() and 1 <= int(choice) <= len(speakers)
            else speakers[0]
        )
        print()

    # Write config
    config: dict = {"music_dir": str(music_dir), "http_port": HTTP_PORT}
    if default_speaker:
        config["default_speaker"] = default_speaker

    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")

    print(f"Config written to:  {CONFIG_FILE}")
    print(f"Music directory:    {music_dir}")
    if default_speaker:
        print(f"Default speaker:    {default_speaker}")
    print(sep + "\n")

    MUSIC_DIR = music_dir


# --- Config helpers ---

def get_default_speaker():
    try:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text()).get("default_speaker")
    except Exception:
        pass
    return None


def save_default_speaker(speaker):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        config = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
        config["default_speaker"] = speaker
        CONFIG_FILE.write_text(json.dumps(config, indent=2))
    except Exception:
        pass


# --- URL helpers ---

def normalize_url(url):
    """Prepend https:// if the URL has no scheme (handles bare youtube.com/... input)."""
    if not url:
        return url
    url = url.strip()
    if url and "://" not in url:
        return "https://" + url
    return url


def is_youtube_url(url):
    try:
        return urlparse(normalize_url(url)).netloc.lower() in _YOUTUBE_HOSTS
    except Exception:
        return False


def is_playlist_url(url):
    if not url:
        return False
    try:
        return "list" in parse_qs(urlparse(normalize_url(url)).query)
    except Exception:
        return False


def clean_youtube_url(url):
    url = normalize_url(url)
    if not url:
        return None
    if "youtu.be" in url:
        return f"https://www.youtube.com/watch?v={url.split('/')[-1].split('?')[0]}"
    try:
        parsed = urlparse(url)
        if "youtube.com" in parsed.netloc:
            params = parse_qs(parsed.query)
            if "/watch" in parsed.path and "v" in params:
                return f"https://www.youtube.com/watch?v={params['v'][0]}"
    except Exception:
        pass
    return url


def read_clipboard():
    for cmd in [["wl-paste"], ["xclip", "-selection", "clipboard", "-o"], ["pbpaste"]]:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            continue
    return None


def resolve_url(url_arg):
    """Return a normalized YouTube URL from: explicit arg → clipboard → stdin prompt.

    Handles bare URLs (without https://). Returns None if nothing usable found.
    """
    if url_arg:
        normalized = normalize_url(url_arg)
        if not is_youtube_url(normalized):
            print(f"Not a recognized YouTube URL: {url_arg}")
            return None
        return normalized

    # Try clipboard first
    clipboard = read_clipboard()
    if clipboard:
        normalized = normalize_url(clipboard)
        if is_youtube_url(normalized):
            return normalized

    # Stdin prompt as fallback (interactive terminals only)
    if sys.stdin.isatty():
        try:
            pasted = input("Paste YouTube URL (or Enter to cancel): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if pasted:
            normalized = normalize_url(pasted)
            if is_youtube_url(normalized):
                return normalized
            print(f"Not a recognized YouTube URL: {pasted}")
    else:
        print("No YouTube URL found. Use -u URL to provide one.")

    return None


def get_video_info(url):
    try:
        result = subprocess.run(
            ["yt-dlp", "--get-title", "--get-duration", "--no-warnings", url],
            capture_output=True, text=True, check=True, timeout=15,
        )
        lines = result.stdout.strip().split("\n")
        return (lines[0] if lines else "Unknown"), (lines[1] if len(lines) > 1 else "Unknown")
    except Exception:
        return None, None


# --- Speaker helpers ---

def discover_sonos_speakers():
    try:
        result = subprocess.run(
            ["sonos-discover"],
            capture_output=True, text=True, check=True, timeout=30,
        )
        speakers = []
        for line in result.stdout.split("\n"):
            if "Visible" in line:
                room = ""
                for part in line.split():
                    if re.match(r"\d+\.\d+\.\d+\.\d+", part):
                        break
                    room += part + " "
                room = room.strip()
                if room:
                    speakers.append(room)
        return sorted(set(speakers))
    except subprocess.TimeoutExpired:
        print("Speaker discovery timed out. Check that your Sonos system is reachable.")
        return []
    except Exception as e:
        print(f"Speaker discovery failed: {e}")
        return []


def resolve_speaker(speaker_arg, quiet=False):
    """Return the speaker name to use.

    Order: explicit -s arg → saved default → auto-discover.
    On first discovery with multiple speakers, prompts interactively if possible.
    Returns None if no speaker can be determined.
    """
    if speaker_arg:
        return speaker_arg

    saved = get_default_speaker()
    if saved:
        return saved

    if not quiet:
        print("No speaker configured. Discovering Sonos speakers...")
    speakers = discover_sonos_speakers()

    if not speakers:
        print("No Sonos speakers found.")
        print("Make sure your Sonos system is on and reachable on the same network.")
        print("Run: play_youtube_url_noson --list-speakers  to test discovery.")
        return None

    if len(speakers) == 1 or quiet or not sys.stdin.isatty():
        speaker = speakers[0]
        save_default_speaker(speaker)
        if not quiet:
            if len(speakers) > 1:
                print(f"Multiple speakers found, using first: {speaker}")
            else:
                print(f"Found speaker: {speaker}")
            print(f"(Saved as default — use -s 'Name' to override)")
        return speaker

    # Interactive multi-speaker picker
    print("Multiple Sonos speakers found:")
    for i, s in enumerate(speakers, 1):
        print(f"  {i}. {s}")
    try:
        choice = input(f"Select [1-{len(speakers)}] or press Enter for '{speakers[0]}': ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        choice = ""

    if choice.isdigit() and 1 <= int(choice) <= len(speakers):
        speaker = speakers[int(choice) - 1]
    else:
        speaker = speakers[0]

    save_default_speaker(speaker)
    print(f"Using: {speaker} (saved as default — use -s 'Name' to override)")
    return speaker


# --- HTTP server daemon ---

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip.startswith("127."):
            print(
                "Warning: detected local IP is loopback — Sonos will not be able to reach"
                f" the HTTP server at {ip}:{HTTP_PORT}.",
                file=sys.stderr,
            )
        return ip
    except Exception:
        print(
            f"Warning: could not detect local IP. Sonos will not be able to reach"
            f" the HTTP server. Check your network connection.",
            file=sys.stderr,
        )
        return "localhost"


def is_http_server_running():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        ok = s.connect_ex(("localhost", HTTP_PORT)) == 0
        s.close()
        return ok
    except Exception:
        return False


def start_http_server_daemon():
    if is_http_server_running():
        return True
    try:
        pid = os.fork()
        if pid > 0:
            time.sleep(0.5)
            return is_http_server_running()
    except OSError:
        return False

    os.setsid()
    os.umask(0)
    try:
        pid = os.fork()
        if pid > 0:
            os._exit(0)
    except OSError:
        os._exit(1)

    sys.stdout.flush()
    sys.stderr.flush()
    devnull_r = os.open(os.devnull, os.O_RDONLY)
    devnull_w = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull_r, 0)
    os.dup2(devnull_w, 1)
    os.dup2(devnull_w, 2)
    os.close(devnull_r)
    os.close(devnull_w)

    class QuietHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(MUSIC_DIR), **kwargs)

        def log_message(self, *args: object, **kwargs: object) -> None:
            _ = args, kwargs

    try:
        with TCPServer(("", HTTP_PORT), QuietHandler) as httpd:
            httpd.serve_forever()
    except Exception:
        pass
    os._exit(0)


# --- yt-dlp upgrade check ---

def check_and_upgrade_ytdlp():
    check_file = MUSIC_DIR / ".ytdlp_last_upgrade_check"
    if check_file.exists() and time.time() - check_file.stat().st_mtime < 86400:
        return
    check_file.touch()

    if not shutil.which("uv") or not shutil.which("yt-dlp"):
        return

    venv_python = Path(sys.executable)
    result = subprocess.run(
        ["uv", "pip", "install", "--upgrade", "yt-dlp", "--python", str(venv_python)],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        for line in (result.stdout + result.stderr).splitlines():
            if "yt-dlp" in line.lower() and ("install" in line.lower() or "upgrad" in line.lower()):
                print(f"[yt-dlp auto-upgraded: {line.strip()}]")
                break


# --- Download ---

def sanitize_filename(title):
    return re.sub(r"[^a-zA-Z0-9_-]", "_", title)


def _first_ytdlp_error(stderr_text):
    """Return the first non-empty, non-debug line from yt-dlp stderr."""
    for line in stderr_text.splitlines():
        line = line.strip()
        if line and not line.startswith("[debug]"):
            return line
    return None


def download_audio(url, title=None, show_progress=True):
    """Download YouTube audio as MP3. Returns (Path, title) or (None, title).

    show_progress=True  — tqdm progress bar in the terminal
    show_progress=False — silent, for use in quiet/scripted contexts
    """
    if not title:
        result = subprocess.run(
            ["yt-dlp", "--get-title", "--no-warnings", url],
            capture_output=True, text=True,
        )
        title = result.stdout.strip() if result.returncode == 0 else None
        if not title:
            return None, title

    sanitized = sanitize_filename(title)
    output_path = MUSIC_DIR / f"{sanitized}.mp3"

    if output_path.exists():
        return output_path, title

    if not show_progress:
        success = False
        first_error = ""
        for fmt in [YTDLP_FORMAT, YTDLP_FORMAT_FALLBACK]:
            result = subprocess.run(
                [
                    "yt-dlp", "--extract-audio", "--audio-format", "mp3",
                    "--audio-quality", "5", "--format", fmt,
                    "-o", str(MUSIC_DIR / f"{sanitized}.%(ext)s"),
                    "--quiet", "--no-warnings", url,
                ],
                capture_output=True, text=True,
            )
            success = result.returncode == 0
            if not success and not first_error:
                first_error = _first_ytdlp_error(result.stderr) or ""
            if success:
                break
            for leftover in MUSIC_DIR.glob(f"{sanitized}.*"):
                try:
                    leftover.unlink()
                except OSError:
                    pass
        if not success:
            print(f"Download failed: {title}")
            if first_error:
                print(f"  Reason: {first_error}")
        return (output_path if output_path.exists() else None), title

    from tqdm import tqdm
    label = title[:55] + "..." if len(title) > 55 else title
    success = False
    last_error_line = ""

    for fmt in [YTDLP_FORMAT, YTDLP_FORMAT_FALLBACK]:
        with tqdm(total=100, desc=label, unit="%") as pbar:
            last_pct = 0.0
            converting = False
            process = subprocess.Popen(
                [
                    "yt-dlp", "--extract-audio", "--audio-format", "mp3",
                    "--audio-quality", "5", "--format", fmt,
                    "-o", str(MUSIC_DIR / f"{sanitized}.%(ext)s"),
                    "--newline", "--no-warnings", url,
                ],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, bufsize=1,
            )
            assert process.stdout is not None
            for line in iter(process.stdout.readline, ""):
                line = line.strip()
                if "[download]" in line and "%" in line:
                    m = re.search(r"(\d+(?:\.\d+)?)%", line)
                    if m:
                        pct = min(float(m.group(1)), 99.0)
                        if pct > last_pct:
                            pbar.update(pct - last_pct)
                            last_pct = pct
                elif "ExtractAudio" in line or ("Destination" in line and ".mp3" in line):
                    if not converting:
                        pbar.set_description(f"Converting: {label}")
                        converting = True
                elif line and not line.startswith("[debug]"):
                    last_error_line = line
            stderr_output = process.stderr.read() if process.stderr else ""
            process.wait()
            if process.returncode == 0:
                pbar.update(100 - last_pct)
                success = True
            else:
                pbar.set_description(f"Failed (retrying): {label}")
                if not last_error_line:
                    last_error_line = _first_ytdlp_error(stderr_output) or ""

        if success:
            break
        for leftover in MUSIC_DIR.glob(f"{sanitized}.*"):
            try:
                leftover.unlink()
            except OSError:
                pass

    if not success:
        print(f"Download failed: {title}")
        if last_error_line:
            print(f"  Reason: {last_error_line}")
        return None, title

    return (output_path if output_path.exists() else None), title


# --- Sonos helpers ---

def mp3_url(mp3_file):
    return f"http://{get_local_ip()}:{HTTP_PORT}/{quote(mp3_file.name)}"
