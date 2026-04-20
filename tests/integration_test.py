#!/usr/bin/env python3
"""
Integration tests for play_youtube_url_noson and queue_youtube_noson.

Runs the CLI scripts end-to-end with stubbed sonos and yt-dlp binaries.
Verifies that correct commands were issued to the Sonos stub.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLAY = REPO / "play_youtube_url_noson"
QUEUE = REPO / "queue_youtube_noson"
STATE = Path("/tmp/sonos_stub_state.json")
SONOS_LOG = Path("/tmp/sonos_stub.log")
YTDLP_LOG = Path("/tmp/ytdlp_stub.log")
TEST_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLtest123"
SPEAKER = "Living Room"

PASS = 0
FAIL = 0
MUSIC_DIR = Path("/tmp/music")
YTDLP_COUNTER = Path("/tmp/ytdlp_stub_download_count")


def reset():
    for f in [STATE, SONOS_LOG, YTDLP_LOG, YTDLP_COUNTER]:
        f.unlink(missing_ok=True)


def clear_music():
    for mp3 in MUSIC_DIR.glob("*.mp3"):
        mp3.unlink()


def run(cmd, expect_rc=0):
    result = subprocess.run(cmd, capture_output=False, text=True)
    if result.returncode != expect_rc:
        print(f"  FAIL: expected rc={expect_rc}, got rc={result.returncode}")
        return False
    return True


def sonos_log():
    return SONOS_LOG.read_text() if SONOS_LOG.exists() else ""


def ytdlp_log():
    return YTDLP_LOG.read_text() if YTDLP_LOG.exists() else ""


def sonos_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {}


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        print(f"  PASS: {label}")
        PASS += 1
    else:
        print(f"  FAIL: {label}{(' -- ' + detail) if detail else ''}")
        FAIL += 1


def test_check_deps():
    print("\n[test] --check-deps")
    reset()
    ok = run([str(PLAY), "--check-deps"])
    check("play --check-deps exits 0", ok)
    ok = run([str(QUEUE), "--check-deps"])
    check("queue --check-deps exits 0", ok)


def test_list_speakers():
    print("\n[test] --list-speakers")
    reset()
    result = subprocess.run([str(PLAY), "--list-speakers"], capture_output=True, text=True)
    check("list-speakers exits 0", result.returncode == 0)
    check("Living Room listed", "Living Room" in result.stdout, result.stdout)
    check("Kitchen listed", "Kitchen" in result.stdout, result.stdout)


def test_play_single():
    print("\n[test] play single video")
    reset()
    ok = run([str(PLAY), "-u", TEST_URL, "-s", SPEAKER])
    check("exits 0", ok)

    log = sonos_log()
    check("sonos-discover was called", "sonos-discover" in log)
    check("stop was issued", f"sonos --use-local-speaker-list {SPEAKER} stop" in log)
    check("play_uri was issued", "play_uri" in log)

    state = sonos_state()
    check("speaker is playing", state.get("playing") is True)

    log_y = ytdlp_log()
    check("yt-dlp was called for title", "--get-title" in log_y)
    check("yt-dlp was called to download", "--extract-audio" in log_y)

    # Verify the MP3 was actually created
    music_dir_line = [l for l in log_y.split("\n") if "Created" in l]
    check("MP3 file was created", len(music_dir_line) > 0, "\n".join(music_dir_line))


def test_play_unknown_speaker():
    print("\n[test] play on unknown speaker")
    reset()
    result = subprocess.run(
        [str(PLAY), "-u", TEST_URL, "-s", "Nonexistent Speaker"],
        capture_output=True, text=True,
    )
    log = sonos_log()
    check("sonos reports unknown speaker", "not found" in log or result.returncode != 0,
          f"rc={result.returncode}")


def test_queue_single():
    print("\n[test] queue single video")
    reset()
    clear_music()
    ok = run([str(QUEUE), "-u", TEST_URL, "-s", SPEAKER])
    check("exits 0", ok)

    log = sonos_log()
    check("add_uri_to_queue was issued", "add_uri_to_queue" in log)

    log_y = ytdlp_log()
    check("yt-dlp downloaded audio", "--extract-audio" in log_y)


def test_queue_playlist():
    print("\n[test] queue playlist")
    reset()
    ok = run([str(QUEUE), "-u", PLAYLIST_URL, "-s", SPEAKER])
    check("exits 0", ok)

    log_y = ytdlp_log()
    check("yt-dlp fetched playlist", "--flat-playlist" in log_y)
    check("yt-dlp downloaded tracks", log_y.count("--extract-audio") >= 1)

    log = sonos_log()
    check("clear_queue was issued", "clear_queue" in log)
    check("play_from_queue was issued", "play_from_queue" in log)
    check("add_uri_to_queue was issued", "add_uri_to_queue" in log)

    state = sonos_state()
    check("queue has tracks", len(state.get("queue", [])) >= 1,
          f"queue length: {len(state.get('queue', []))}")


def test_play_next():
    print("\n[test] queue --play-next")
    reset()
    ok = run([str(QUEUE), "-u", TEST_URL, "-s", SPEAKER, "--play-next"])
    check("exits 0", ok)

    log = sonos_log()
    check("queue_position was queried", "queue_position" in log)
    check("next was issued", f"sonos --use-local-speaker-list {SPEAKER} next" in log)


def test_cached_track():
    print("\n[test] cached track reuse")
    # First play to populate cache
    reset()
    run([str(PLAY), "-u", TEST_URL, "-s", SPEAKER])

    # Second play should skip download
    YTDLP_LOG.unlink(missing_ok=True)
    run([str(PLAY), "-u", TEST_URL, "-s", SPEAKER])

    log_y = ytdlp_log()
    check("second play skips download (no --extract-audio)", "--extract-audio" not in log_y,
          log_y)


def test_missing_music_dir():
    print("\n[test] missing music directory")
    reset()

    with tempfile.TemporaryDirectory() as tmpdir:
        fake_home = Path(tmpdir)
        config_dir = fake_home / ".config"
        config_dir.mkdir()
        absent_music = fake_home / "nonexistent_music"
        (config_dir / "youtube_to_sonos_config.json").write_text(json.dumps({
            "default_speaker": SPEAKER,
            "music_dir": str(absent_music),
            "http_port": 8765,
        }))

        result = subprocess.run(
            [str(QUEUE), "-u", TEST_URL, "-s", SPEAKER, "-q"],
            capture_output=True, text=True,
            env={**os.environ, "HOME": str(fake_home)},
        )

    output = result.stdout + result.stderr
    informed = (
        str(absent_music) in output
        or "does not exist" in output
        or result.returncode != 0
    )
    check("quiet mode: fails cleanly when music dir missing", informed,
          f"rc={result.returncode} out={output!r}")


def test_bare_url():
    """Bare URL without https:// should work the same as a full URL."""
    print("\n[test] bare URL (no https://)")
    reset()
    clear_music()
    bare = "youtube.com/watch?v=dQw4w9WgXcQ"
    ok = run([str(PLAY), "-u", bare, "-s", SPEAKER])
    check("exits 0 with bare URL", ok)

    log = sonos_log()
    check("play_uri issued for bare URL", "play_uri" in log)

    log_y = ytdlp_log()
    check("yt-dlp downloaded with bare URL", "--extract-audio" in log_y)


def test_queue_playlist_first_track_fails():
    """If the first track fails to download, later tracks must still clear and use the queue."""
    print("\n[test] queue playlist: first track fails, queue still starts")
    reset()

    env = {**os.environ, "YTDLP_STUB_FAIL_TRACK": "1"}
    result = subprocess.run(
        [str(QUEUE), "-u", PLAYLIST_URL, "-s", SPEAKER],
        capture_output=True, text=True, env=env,
    )

    log = sonos_log()
    # Even when the first track fails, prepare_queue (stop + clear) must have been called
    check("clear_queue issued despite first-track failure", "clear_queue" in log,
          f"rc={result.returncode}")
    check("play_from_queue issued after first failure", "play_from_queue" in log,
          f"log={log!r}")


def test_malformed_config():
    """Malformed config JSON should warn and fall back to defaults, not crash."""
    print("\n[test] malformed config file")
    reset()
    clear_music()

    with tempfile.TemporaryDirectory() as tmpdir:
        fake_home = Path(tmpdir)
        config_dir = fake_home / ".config"
        config_dir.mkdir()
        (config_dir / "youtube_to_sonos_config.json").write_text("{ not valid json }")

        result = subprocess.run(
            [str(PLAY), "-u", TEST_URL, "-s", SPEAKER],
            capture_output=True, text=True,
            env={**os.environ, "HOME": str(fake_home)},
        )

    output = result.stdout + result.stderr
    check("warns about malformed config", "Warning" in output or "parse" in output.lower(),
          f"rc={result.returncode} out={output!r}")
    check("does not crash (exits 0)", result.returncode == 0,
          f"rc={result.returncode}")


def test_non_youtube_url():
    """Non-YouTube URLs should be rejected with a clear error, not crash."""
    print("\n[test] non-YouTube URL rejected")
    reset()
    result = subprocess.run(
        [str(PLAY), "-u", "https://example.com/watch?v=abc"],
        capture_output=True, text=True,
    )
    output = result.stdout + result.stderr
    check("rejects non-YouTube URL", result.returncode != 0 or "Not a recognized" in output,
          f"rc={result.returncode} out={output!r}")
    log = sonos_log()
    check("no sonos command issued for bad URL", "play_uri" not in log)


def test_first_run_wizard_skipped_non_interactive():
    """In non-interactive mode, wizard must not block waiting for input."""
    print("\n[test] first-run wizard skipped in non-interactive mode")
    reset()

    with tempfile.TemporaryDirectory() as tmpdir:
        fake_home = Path(tmpdir)
        # Config points to existing music dir so ensure_music_dir passes
        config_dir = fake_home / ".config"
        config_dir.mkdir()
        (config_dir / "youtube_to_sonos_config.json").write_text(json.dumps({
            "default_speaker": SPEAKER,
            "music_dir": str(MUSIC_DIR),
            "http_port": 8765,
        }))
        # Remove config so wizard would trigger — but stdin is non-interactive
        (config_dir / "youtube_to_sonos_config.json").unlink()

        result = subprocess.run(
            [str(PLAY), "-u", TEST_URL, "-s", SPEAKER],
            capture_output=True, text=True,
            stdin=subprocess.DEVNULL,  # non-interactive — wizard must be skipped
            env={**os.environ, "HOME": str(fake_home)},
        )

    output = result.stdout + result.stderr
    check("wizard not shown in non-interactive mode", "First-time setup" not in output)
    # Script may abort due to missing music dir (default path under fake_home) — that is fine.
    # The key invariant is that it does NOT hang waiting for input.
    check("exits without hanging (rc is 0 or 1)", result.returncode in (0, 1),
          f"rc={result.returncode}")


def test_uv_missing_gives_clear_guidance():
    """When uv and yt-dlp are both absent, check_dependencies mentions uv."""
    print("\n[test] missing uv gives clear install guidance")

    # Run check_dependencies() directly with shutil.which patched so both
    # uv and yt-dlp appear missing. This avoids PATH manipulation that would
    # break the shebang interpreter lookup.
    venv_python = REPO / ".venv" / "bin" / "python3"
    result = subprocess.run(
        [
            str(venv_python), "-c",
            (
                "import shutil; _w = shutil.which\n"
                "shutil.which = lambda n: None if n in ('uv', 'yt-dlp') else _w(n)\n"
                "import sys; sys.path.insert(0, '.')\n"
                "from yt_sonos import check_dependencies\n"
                "issues = check_dependencies()\n"
                "print('\\n'.join(issues))\n"
                "sys.exit(1 if issues else 0)\n"
            ),
        ],
        capture_output=True, text=True, cwd=str(REPO),
    )
    output = result.stdout + result.stderr
    check("exits non-zero when uv missing", result.returncode != 0,
          f"rc={result.returncode}")
    check("mentions uv in error output", "uv" in output.lower(),
          f"out={output!r}")
    check("provides install command", "curl" in output or "install.sh" in output,
          f"out={output!r}")


def main():
    print("Integration tests — youtube-to-sonos")
    print("=====================================")

    test_check_deps()
    test_list_speakers()
    test_play_single()
    test_play_unknown_speaker()
    test_queue_single()
    test_queue_playlist()
    test_play_next()
    test_cached_track()
    test_missing_music_dir()
    test_bare_url()
    test_queue_playlist_first_track_fails()
    test_malformed_config()
    test_non_youtube_url()
    test_first_run_wizard_skipped_non_interactive()
    test_uv_missing_gives_clear_guidance()

    print(f"\n{'='*37}")
    print(f"Results: {PASS} passed, {FAIL} failed")

    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
