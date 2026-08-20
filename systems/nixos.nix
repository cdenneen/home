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
      homeModules = host.homeModules or [ ];
      nixosModules = host.modules;
      tags = host.tags or [ ];
      agentPkgsOverride = host.agentPkgsOverride or (agentPkgs: _unstablePkgs: agentPkgs);
    }
  ) hostCatalog.nixosByName;

  nixosConfigurations = allNixosConfigurations;
in
{
  nixosSystem = mkNixosSystem;
  inherit allNixosConfigurations nixosConfigurations;
}
