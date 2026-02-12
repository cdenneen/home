{ self, ... }@inputs:
let
  lib = import ./lib.nix { inherit inputs self; };
  nixos = import ./nixos.nix { inherit inputs self lib; };
  darwin = import ./darwin.nix { inherit inputs self lib; };
  home = import ./home.nix { inherit inputs self lib; };
in
{
  lib = {
    inherit (darwin) darwinSystem;
    inherit (home) homeConfiguration;
    inherit (nixos) nixosSystem;
  };

  inherit (darwin) darwinConfigurations;
  inherit (home) homeConfigurations;
  inherit (nixos) nixosConfigurations;
}
