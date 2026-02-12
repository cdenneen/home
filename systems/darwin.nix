{
  inputs,
  self,
  lib,
}:
let
  inherit (inputs)
    home-manager
    mac-app-util
    nix-darwin
    nix-index-database
    nixpkgs
    nur
    sops-nix
    ;
  inherit (lib) mkPkgs sharedHomeModulesIntegrated;

  darwinSystem =
    {
      system,
      darwinModules ? [ ],
      homeModules ? [ ],
    }:
    let
      pkgsSet = mkPkgs system;
      stablePkgs = pkgsSet.stable;
      unstablePkgs = pkgsSet.unstable;
    in
    nix-darwin.lib.darwinSystem rec {
      pkgs = stablePkgs;
      specialArgs = inputs // {
        inherit system stablePkgs unstablePkgs;
      };
      modules = [
        self.commonModules.users.cdenneen
        home-manager.darwinModules.default
        inputs.nix-homebrew.darwinModules.nix-homebrew
        mac-app-util.darwinModules.default
        nix-index-database.darwinModules.nix-index
        nur.modules.darwin.default
        self.darwinModules.default
        sops-nix.darwinModules.sops
        {
          home-manager = {
            extraSpecialArgs = specialArgs;
            sharedModules = [
              mac-app-util.homeManagerModules.default
            ]
            ++ homeModules
            ++ sharedHomeModulesIntegrated;
          };
          homebrew = {
            enable = true;
            user = "cdenneen";
          };
        }
      ]
      ++ darwinModules;
    };

  darwinConfigurations = {
    VNJTECMBCD = darwinSystem {
      system = "aarch64-darwin";
      darwinModules = [ ./VNJTECMBCD.nix ];
    };
  };
in
{
  inherit darwinConfigurations darwinSystem;
}
