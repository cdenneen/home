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
      darwinModules = host.modules;
    }
  ) hostCatalog.darwinByName;
in
{
  darwinSystem = mkDarwinSystem;
  inherit darwinConfigurations;
}
