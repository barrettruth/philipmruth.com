{
  description = "philipmruth.com";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    systems.url = "github:nix-systems/default";
  };

  outputs =
    {
      nixpkgs,
      systems,
      ...
    }:
    let
      forEachSystem =
        f: nixpkgs.lib.genAttrs (import systems) (system: f nixpkgs.legacyPackages.${system});
    in
    {
      formatter = forEachSystem (pkgs: pkgs.nixfmt-tree);

      devShells = forEachSystem (
        pkgs:
        let
          pythonEnv = pkgs.python3.withPackages (
            ps: with ps; [
              pillow
              pillow-heif
              rich
              typer
            ]
          );
          commonPackages = [
            pkgs.nodejs_22
            pkgs.openssh
            pkgs.pnpm
            pkgs.just
            pkgs.rsync
            pkgs.uv
            pkgs.exiftool
            pkgs.libheif
            pkgs.imagemagick
            pkgs.vips
            pkgs.libwebp
            pythonEnv
          ];
        in
        {
          default = pkgs.mkShell { packages = commonPackages; };
          ci = pkgs.mkShell { packages = commonPackages; };
        }
      );
    };
}
