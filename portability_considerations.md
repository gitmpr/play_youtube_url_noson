# Portability Considerations

This document examines how portable and relocatable youtube-to-sonos is today,
what already helps (the move from pipx to uv), where we are still tied to the
host system, and a proposed Nix-based approach that would address most of it.

It is a design and reasoning document, not a how-to. The Nix section is a plan
only and is intentionally not implemented yet.

---

## 1. Cross-platform portability

The project is two `#!/usr/bin/env python3` entry scripts plus a shared
`yt_sonos.py`, backed by a uv-managed virtualenv. Portability is limited less by
the Python code style (it uses `pathlib`, `shutil.which`, and `subprocess`
throughout) and more by a handful of POSIX-specific assumptions and external
command dependencies.

### Linux distributions: well supported

This is the primary target and the most portable case. Requirements:

- Some `python3` on `PATH` to run the 12-line re-exec preamble (see section 3).
- `uv` to create the venv (self-contained, single static binary).
- `ffmpeg` for MP3 conversion, installed via the distro package manager.
- A clipboard reader: `wl-paste` (Wayland) or `xclip` (X11). Optional; only the
  clipboard URL-input path needs it.

Nothing here is Ubuntu-specific. `install.sh` prints `apt`/`brew` hints but the
actual dependency is just the binaries being on `PATH`, so Fedora, Arch, Debian,
openSUSE, etc. all work once those packages are present. The HTTP daemon, the
`~/.config` config location, and the `.venv/bin` PATH prepend are all standard
across distros.

### macOS: works with minor caveats

macOS is POSIX, so the structurally risky parts (fork-based daemon, `.venv/bin`
layout, readline, `os.execvp`) all function. Specific notes:

- Clipboard works out of the box: `pbpaste` is in the fallback list.
- `ffmpeg` comes from Homebrew, which `install.sh` already documents.
- Config lives at `~/.config/youtube_to_sonos_config.json`. This is not the
  native macOS location (`~/Library/Application Support`), but the code uses
  `~/.config` explicitly, so it is consistent and works fine.
- Sonos discovery relies on UPnP SSDP multicast on the LAN. macOS firewalls and
  "Local Network" privacy permissions can block this; it is an environmental
  caveat rather than a code issue.

### Windows: multiple hard blockers

Windows is currently unsupported, and not by a small margin. The blockers are
structural, not cosmetic:

- **Fork-based HTTP daemon.** `start_http_server_daemon()` uses the classic
  double `os.fork()` + `os.setsid()` POSIX daemon idiom (`yt_sonos.py:502`,
  `:509`, `:512`). `os.fork` does not exist on Windows. Serving MP3s to Sonos
  would need a completely different detachment mechanism (a detached
  `subprocess` with `CREATE_NEW_PROCESS_GROUP`/`DETACHED_PROCESS`, or a
  background thread).
- **venv layout.** The re-exec hardcodes `.venv/bin/python3`. On Windows the
  interpreter is `.venv\Scripts\python.exe`, so the re-exec never fires.
- **`os.execvp` delegation.** Playlist URLs in `play_youtube_url_noson` hand off
  to `queue_youtube_noson` via `os.execvp` (`play_youtube_url_noson:107`).
  Windows has no true `exec` process replacement; the emulated behaviour is
  unreliable for a console tool.
- **readline.** The first-run wizard prefill uses `readline.set_startup_hook`
  (`yt_sonos.py:167`). The stdlib `readline` module is not available on Windows
  without `pyreadline3`.
- **Clipboard.** None of `wl-paste`, `xclip`, `pbpaste` exist on Windows; it
  would need `Get-Clipboard` / `clip`.
- **install.sh** is bash.

Supporting Windows would mean abstracting the daemon, the venv path, the
delegation, and the clipboard behind platform checks. It is feasible but is a
real porting effort, not a tweak. WSL is the pragmatic escape hatch: under WSL
the project runs as plain Linux.

### Summary matrix

| Concern                  | Linux | macOS | Windows |
|--------------------------|-------|-------|---------|
| Core download + playback | Yes   | Yes   | No (fork daemon, venv path) |
| Clipboard URL input      | Yes   | Yes   | No      |
| First-run wizard prefill | Yes   | Yes   | No (readline) |
| Playlist delegation      | Yes   | Yes   | No (execvp) |
| install.sh               | Yes   | Yes   | No (bash) |

---

## 2. Movability of the repository directory

The repo directory is **not** freely movable today, and this already bit this
project once: it was created at one path, later moved into a deeper namespace,
and the in-tree `.venv` carried stale absolute paths that broke `sonos` and
`yt-dlp` with `bad interpreter: No such file`.

### Why moving breaks the venv

This is standard Python virtualenv behaviour, not specific to uv. Two things bake
in absolute paths at venv creation time:

1. **Console-script shebangs.** Every wrapper in `.venv/bin/` (`sonos`,
   `sonos-discover`, `yt-dlp`) begins with
   `#!/abs/path/to/.venv/bin/python`. The Linux kernel requires a shebang
   interpreter to be an absolute path, so it cannot be relative.
2. **`pyvenv.cfg` `home =`** and the `bin/python` symlink are absolute too.

Move the directory and those absolute paths now point at a location that no
longer exists.

### What is already relative, and what saves us partially

The entry-script preamble is deliberately relative:

```python
_here = Path(__file__).resolve().parent
_venv_python = _here / ".venv/bin/python3"
...
os.execv(str(_venv_python), [str(_venv_python)] + sys.argv)
```

`_venv_python` is resolved relative to the script, and `.venv/bin/python3` is a
symlink to the uv-managed standalone interpreter that lives **outside** the repo
(`~/.local/share/uv/python/cpython-3.13.6-.../bin/python3.13`). That symlink
target is unaffected by a repo move. So after moving the repo, the launcher
still re-execs into Python correctly and imports `yt_sonos` correctly. What
breaks is the next layer: it then tries to run `sonos`, whose console-script
shebang still points at the old repo path.

In other words, the relative re-exec masks half the problem and makes the
remaining failure more confusing, because the tool gets far enough to download
and only fails at the Sonos call.

### Fixing movability

Three options, in increasing order of robustness:

1. **Treat `.venv` as disposable (recommended default).** `.venv` is already
   gitignored. After any move, run `uv sync` (about one second) to regenerate
   the venv with correct paths. This is the intended uv workflow and is what we
   did to repair the canonical clone. The mental model: the venv is a build
   artifact, not source.
2. **`uv venv --relocatable`.** uv can build a relocatable venv that resolves
   its interpreter relative to the script location instead of via a hardcoded
   absolute shebang (a small launcher/trampoline shim), at the cost of slightly
   higher process startup time. This lets you move the directory on the same
   machine without re-syncing. Caveat: it makes the venv's internal references
   relocatable, but the venv still points at the external uv-managed interpreter
   via `pyvenv.cfg`. Moving to a different machine, or after garbage-collecting
   that interpreter, still needs a `uv sync`. Relocatable solves "moved the
   folder", not "copied to a fresh host".
3. **Do not ship a venv at all; resolve at runtime via `uv run`.** Invoke the
   scripts through `uv run` so uv provisions the environment on demand from
   `pyproject.toml` + `uv.lock`. This removes the in-tree venv as a movable
   artifact entirely, at the cost of a uv invocation on each run.

The trampoline mechanism in option 2 is the most interesting middle ground and
is worth a small experiment, but option 1 is the pragmatic answer for this
project's usage.

---

## 3. uv versus pip / pipx, and the residual tie to system Python

### What the move to uv already fixed

The original Sonos tooling was a global `pipx install soco-cli`. pipx builds its
venv against whatever interpreter it was told to use, which here was the system
(apt) Python. When Ubuntu upgraded the system `python3` from 3.12 to 3.14, the
pipx venv's packages were stranded in `lib/python3.12/site-packages` while its
`python` symlink now resolved to 3.14, which looks only in `lib/python3.14`.
Result: `ModuleNotFoundError: No module named 'soco_cli'` on every call. The
dependency was silently destroyed by an unrelated OS update.

The uv project venv is decoupled from this failure mode because uv does not use
the system Python at all. It downloads and pins a **standalone** CPython build
(here `cpython-3.13.6`, living in `~/.local/share/uv/python/`) and the venv
points at that. An apt upgrade, removal, or version bump of the system
interpreter does not touch it. Combined with `uv.lock`, the dependency set is
both reproducible and insulated from the host Python. This is the single biggest
portability and reliability win we already have.

### Where we are still tied to the host Python

Not fully decoupled. The entry scripts start with `#!/usr/bin/env python3`. That
bootstrap needs *some* `python3` on `PATH` to execute the short preamble before
it re-execs into the uv interpreter. In practice this is the system (apt) Python.
Implications:

- The preamble only imports `sys`, `pathlib`, and `os`, so it runs under
  essentially any Python 3, and the heavy dependencies never load under it.
  The tie is therefore thin: we need a working `python3`, not a specific version.
- But it is still a tie. If the system Python were removed entirely, the
  launcher could not bootstrap, even though the uv interpreter is intact.
- It could be eliminated by pointing the installed entry points directly at the
  uv interpreter (for example, generating the `~/.local/bin` entry as a tiny
  wrapper that calls `.venv/bin/python3` directly, or letting uv produce the
  console scripts), removing `/usr/bin/env python3` from the critical path.

So: the dependencies are decoupled from system Python; the launcher bootstrap is
not yet.

---

## 4. How Nix could solve most of this (plan, not implemented)

Nix is attractive here because it makes the runtime closure explicit and
content-addressed: the interpreter, every Python dependency, `ffmpeg`, and the
clipboard tools all become fixed store paths that are built once and never
mutate underneath us. That directly addresses the two failures this project has
already hit (an OS Python upgrade breaking deps, and a directory move breaking a
venv), because there is no in-tree venv and nothing depends on the ambient
system Python.

This fits the existing machine setup, which already uses Nix plus home-manager
with a layered config (nix-config installs packages; public dotfiles provide
tool configs).

### What Nix would and would not solve

- Solves: reproducible dependency closure, immunity to system Python changes,
  no movable `.venv` (the app is built into the store), guaranteed presence of
  `ffmpeg` and a clipboard tool via wrapper, declarative install through
  home-manager with no manual `~/.local/bin` symlinks.
- Does not solve: Windows. Nix targets Linux and macOS (nix-darwin). The
  fork/readline/execvp/clipboard issues from section 1 are orthogonal to
  packaging and remain if Windows support is ever wanted.

### Building blocks already available in nixpkgs

Verified present, so the package does not require vendoring:

- `soco-cli` (0.4.85, top-level) and `python3Packages.soco` (0.31.1)
- `yt-dlp`, `python3Packages.tqdm`
- `ffmpeg`, `wl-clipboard` / `xclip`

The packaged `soco-cli` is 0.4.85 against our pinned 0.4.86, which is close
enough to start; an override can pin an exact version if needed.

### Proposed plan

1. **Add a `flake.nix`** to the repo exposing a `packages.default` and an
   `apps` entry for each script, plus a `devShells.default` for development that
   keeps the current `uv` workflow intact. A flake also pins nixpkgs for
   reproducibility.

2. **Choose the dependency route.** Two viable options:
   - **Route A, nixpkgs-native (simplest).** Build a `python3.withPackages`
     environment from nixpkgs (`soco`, `tqdm`, `yt-dlp`) and install `soco-cli`
     from nixpkgs as a separate binary on the wrapper's `PATH`. Lowest
     maintenance; accepts nixpkgs' versions.
   - **Route B, lockfile-faithful via uv2nix / pyproject-nix.** Consume the
     existing `pyproject.toml` and `uv.lock` so the Nix build matches the exact
     pinned versions we already test with. More setup, but keeps a single source
     of truth for versions and avoids drift between the uv and Nix worlds.
   Recommendation: start with Route A to prove the packaging, evaluate Route B
   if version drift becomes a real problem.

3. **Wrap the scripts with `makeWrapper`.** Install
   `play_youtube_url_noson` and `queue_youtube_noson` as wrapped binaries whose
   `PATH` is prefixed with the closure's `ffmpeg`, `soco-cli`, and a clipboard
   tool. This replaces the current ad-hoc `.venv/bin` PATH prepend with a
   guaranteed runtime closure and removes the `/usr/bin/env python3` bootstrap
   tie entirely, since the wrapper invokes the Nix-provided interpreter directly.

4. **Drop the in-tree venv from the runtime path.** Under Nix there is no
   `.venv` to move or break. `uv` remains only for local development via the
   dev shell. This resolves section 2 by construction.

5. **Integrate with home-manager.** Add the flake package to `home.packages`
   (or via an overlay) in the nix-config layer, so the two commands land on
   `PATH` through the Nix profile. No more manual `~/.local/bin` symlinks, and
   the install becomes declarative and reproducible across machines.

6. **Leave runtime concerns unchanged.** The HTTP daemon, `~/.config` config
   file, and Sonos discovery are runtime behaviours that Nix does not need to
   alter. The config file stays at `~/.config/youtube_to_sonos_config.json` and
   remains user-owned, outside the store.

7. **Optional: macOS via nix-darwin.** The same flake package builds on
   `aarch64-darwin` / `x86_64-darwin`, giving a single declarative definition
   for both Linux and macOS, with Homebrew no longer needed for `ffmpeg`.

### devenv: separating the dev environment from the packaging strategy

Route A/B above treat packaging and the dev shell as one flake. A cleaner mental
model splits the two concerns explicitly, and [devenv](https://devenv.sh) is built
around exactly that split:

- **Development environment** — `devenv` with its Python integration manages the
  interpreter and lets `uv` handle local dependency resolution, so day-to-day
  iteration stays fast and lockfile-driven:

  ```nix
  languages.python = {
    enable = true;
    version = "3.13";
    uv.enable = true;
    uv.sync.enable = true;   # runs `uv sync` on shell entry
  };
  ```

- **Packaging output** — the production package is built from `pyproject.toml` +
  `uv.lock` via uv2nix, exposed as a devenv `outputs.*` attribute. This is Route B
  by another name: the package and the dev shell share one source of truth
  (`uv.lock`), so `soco-cli`/`yt-dlp` come from the locked versions we actually
  test with, not from whatever nixpkgs happens to ship.

The appeal is the single split: `uv` drives both layers (fast dev sync, exact
packaging), and the wrapper's runtime closure then only needs the non-Python
tools (`ffmpeg`, clipboard), since `sonos`/`yt-dlp` are console scripts in the
uv2nix-built environment.

Caveats:

- This is heavier than the ~40-line Route A flake: extra inputs (devenv, uv2nix,
  pyproject-nix) and likely a few uv2nix build-system overrides, because
  `soco-cli` pulls `cryptography`/`fastapi`/`uvicorn` which sometimes need
  fixups. For a three-file project it is arguably over-engineered, but it is a
  clean reference for the "one lockfile, two consumers" pattern.
- The exact devenv API for the packaging output (e.g. a
  `languages.python`-provided build helper vs. wiring uv2nix directly) has moved
  across devenv releases. Verify it against the current devenv docs before
  relying on a specific attribute name; the dev-side `uv.sync.enable` is stable.

### Net effect

After this, the failure modes that motivated this document disappear: there is
no system-Python coupling, no relocatable-venv problem, and no manual symlink
wiring. The cost is maintaining a flake and (for Route B) a uv-to-Nix bridge.
The remaining non-portable surface is Windows, which Nix does not address and
which would still require the code-level changes in section 1.

---

## 5. C library (libc) and ABI compatibility

The sections above treat "Linux" as one target, but Linux ships three different C
libraries, and the project's runtime closure is bound to whichever one it was
built against:

- **glibc** — mainstream distros (Ubuntu, Debian, Fedora, Arch).
- **musl** — Alpine and most minimal container images.
- **bionic** — Android / Termux.

The project's own code (the launchers and `yt_sonos.py`) is pure Python and
libc-agnostic. The libc dependence enters entirely through the *closure*: the
interpreter, the native wheels, and `ffmpeg`.

### Where libc bites

- **uv-managed CPython.** uv does not compile Python; it downloads a
  python-build-standalone binary, published per `(architecture, libc)` target
  (`x86_64-unknown-linux-gnu` for glibc, `x86_64-unknown-linux-musl` for musl,
  plus the aarch64 variants). uv selects the right one for the host, but the glibc
  build also assumes a minimum host glibc version, and **there is no bionic
  build** at all.
- **Native wheels.** `soco-cli` pulls `cryptography`, and `yt-dlp` pulls
  `curl-cffi`/`curl-impersonate` — both ship compiled extensions as `manylinux`
  (glibc) or `musllinux` (musl) wheels. pip/uv pick the matching tag per platform.
  There is no bionic wheel tag; on native Termux you would build from source
  against bionic or use Termux's own packaged versions.
- **ffmpeg** is a native binary linked against its own libc.

### Consequence for movability (extends section 2)

A built `.venv`, or any copied closure, is **not portable across libc families**,
and often not even across glibc *versions* (a venv built on a newer glibc fails on
an older host with `GLIBC_x.xx not found`). This reinforces the "regenerate, do
not move" rule: `uv sync` on the target host fetches the libc variant that host
needs. Copying the tree between a glibc laptop and an Alpine container, or to
Android, will not work; re-syncing will.

### Android / Termux specifically

Native bionic is the hardest target: no standalone Python, no manylinux/musllinux
wheels, no prebuilt `cryptography`/`curl-cffi`. The practical path on Android is a
proot'd glibc userland (nix-on-droid, or proot-distro), where everything behaves
like ordinary glibc Linux. So "running this on Android" in practice means "running
it inside a glibc sandbox on Android," not on native bionic.

### Nix angle

On a glibc host, Nix brings its **own** glibc into the store and links every
binary in the closure against it, so nix-built binaries are independent of the
host glibc version — no `GLIBC_x.xx` mismatch, which is a real robustness gain
over the uv standalone build that depends on the host glibc baseline. musl is
available through the nixpkgs musl overlay but is niche; Android is again the
nix-on-droid proot-glibc route. So Nix narrows the libc problem on glibc systems
but does not make a single closure span glibc/musl/bionic.

### Net

The libc dimension does not change the code, but it bounds what "portable" means:
the project is portable, its dependency closure is libc-specific, and the answer
on every front is the same as for directory moves: rebuild per platform rather
than copy a built environment.
