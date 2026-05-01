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
          commonPackages = [
            pkgs.nodejs_22
            pkgs.openssh
            pkgs.pnpm
            pkgs.just
            pkgs.rsync
          ];
        in
        {
          default = pkgs.mkShell { packages = commonPackages; };
          ci = pkgs.mkShell { packages = commonPackages; };
        }
      );
    };
}
