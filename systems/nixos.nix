{
  inputs,
  self,
  lib,
  hostCatalog ? import ../hosts,
}:
let
  inherit (lib) mkNixosSystem;

  allNixosConfigurations = builtins.mapAttrs (
    _: host:
    mkNixosSystem {
      system = host.system;
      nixosModules = host.modules;
      tags = host.tags or [ ];
    }
  ) hostCatalog.nixosByName;

  nixosConfigurations = allNixosConfigurations;
in
{
  nixosSystem = mkNixosSystem;
  inherit allNixosConfigurations nixosConfigurations;
}
