{ lib
, stdenvNoCC
, makeWrapper
, python3
, ffmpeg
, soco-cli
, yt-dlp
, wl-clipboard
, xclip
}:

let
  # The only third-party import in the code is tqdm; yt-dlp and sonos are
  # invoked as external commands, so they go on PATH via the wrapper rather
  # than into the Python environment.
  pythonEnv = python3.withPackages (ps: [ ps.tqdm ]);

  # Clipboard helpers are Linux-only. On macOS the code falls back to the
  # system pbpaste, which is always present and is not a Nix package.
  clipboardTools = lib.optionals stdenvNoCC.hostPlatform.isLinux [ wl-clipboard xclip ];

  runtimeDeps = [ ffmpeg soco-cli yt-dlp ] ++ clipboardTools;
in
stdenvNoCC.mkDerivation {
  pname = "youtube-to-sonos";
  version = "0.1.0";

  src = ./.;

  nativeBuildInputs = [ makeWrapper ];

  # Plain scripts: nothing to configure or compile.
  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall

    libexec=$out/libexec/youtube-to-sonos
    mkdir -p "$libexec" $out/bin

    install -m644 yt_sonos.py "$libexec/yt_sonos.py"
    install -m755 play_youtube_url_noson queue_youtube_noson "$libexec/"

    # Each command is a wrapper that runs the script under the pinned Python
    # interpreter, with the runtime closure (ffmpeg, soco-cli, yt-dlp, clipboard)
    # and the package's own bin on PATH. The latter lets play delegate playlists
    # to queue via os.execvp regardless of the ambient PATH.
    for script in play_youtube_url_noson queue_youtube_noson; do
      makeWrapper ${pythonEnv}/bin/python3 $out/bin/$script \
        --add-flags "$libexec/$script" \
        --prefix PATH : "${lib.makeBinPath runtimeDeps}:$out/bin"
    done

    runHook postInstall
  '';

  meta = {
    description = "Play and queue YouTube audio on Sonos speakers via CLI";
    homepage = "https://github.com/gitmpr/play_youtube_url_noson";
    license = lib.licenses.mit;
    mainProgram = "play_youtube_url_noson";
    platforms = lib.platforms.unix;
  };
}
