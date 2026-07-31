{
  inputs,
  self,
  lib,
  hostCatalog ? import ../hosts,
}:
let
  inherit (lib) mkDarwinSystem;

  darwinConfigurations = builtins.mapAttrs (
    _: host:
    mkDarwinSystem {
      system = host.system;
      homeModules = host.homeModules or [ ];
      darwinModules = host.modules;
      legacyBigSur = host.legacyBigSur or false;
    }
  ) hostCatalog.darwinByName;
in
{
  darwinSystem = mkDarwinSystem;
  inherit darwinConfigurations;
}
