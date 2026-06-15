{
  description = "Play and queue YouTube audio on Sonos speakers via CLI";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = f:
        nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAllSystems (pkgs: {
        default = pkgs.callPackage ./package.nix { };
        youtube-to-sonos = pkgs.callPackage ./package.nix { };
      });

      apps = forAllSystems (pkgs:
        let pkg = pkgs.callPackage ./package.nix { };
        in {
          default = {
            type = "app";
            program = "${pkg}/bin/play_youtube_url_noson";
          };
          play = {
            type = "app";
            program = "${pkg}/bin/play_youtube_url_noson";
          };
          queue = {
            type = "app";
            program = "${pkg}/bin/queue_youtube_noson";
          };
        });

      # Development uses the existing uv workflow; this shell provides uv plus
      # the external runtime tools so `uv run ./play_youtube_url_noson` works.
      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [ pkgs.uv pkgs.ffmpeg pkgs.soco-cli pkgs.yt-dlp ];
        };
      });

      formatter = forAllSystems (pkgs: pkgs.nixpkgs-fmt);
    };
}
