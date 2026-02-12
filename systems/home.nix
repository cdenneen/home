{
  inputs,
  self,
  lib,
}:
let
  inherit (inputs) home-manager;
  inherit (lib) mkPkgs sharedHomeModulesStandalone;

  defaultHomeModule =
    { pkgs, ... }:
    {
      home.username = "cdenneen";
      home.homeDirectory = if pkgs.stdenv.isDarwin then "/Users/cdenneen" else "/home/cdenneen";
      profiles.defaults.enable = true;
      profiles.gui.enable = pkgs.stdenv.isDarwin;
    };

  homeConfiguration =
    {
      system,
      homeModules ? [ ],
    }:
    let
      pkgsSet = mkPkgs system;
      stablePkgs = pkgsSet.stable;
      unstablePkgs = pkgsSet.unstable;
    in
    home-manager.lib.homeManagerConfiguration {
      pkgs = unstablePkgs;
      extraSpecialArgs = inputs // {
        inherit system stablePkgs unstablePkgs;
      };
      modules = homeModules ++ sharedHomeModulesStandalone;
    };

  homeConfigurations = {
    # Default Linux (aarch64) target
    cdenneen = homeConfiguration {
      system = "aarch64-linux";
      homeModules = [
        defaultHomeModule
      ];
    };
    cdenneen-x86_64-linux = homeConfiguration {
      system = "x86_64-linux";
      homeModules = [
        defaultHomeModule
      ];
    };
    cdenneen-aarch64-darwin = homeConfiguration {
      system = "aarch64-darwin";
      homeModules = [
        defaultHomeModule
      ];
    };
  };
in
{
  inherit homeConfigurations homeConfiguration;
}
