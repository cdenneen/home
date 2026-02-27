{
  inputs,
  self,
  lib,
}:
let
  inherit (lib) mkNixosSystem extraModulesForTags;

  hostDefs = import ../hosts;

  allNixosConfigurations = builtins.listToAttrs (
    map (host: {
      name = host.name;
      value = mkNixosSystem {
        system = host.system;
        nixosModules = host.modules;
        tags = host.tags or [ ];
      };
    }) hostDefs.nixos
  );

  nixosConfigurations = builtins.listToAttrs (
    map (host: {
      name = host.name;
      value = allNixosConfigurations.${host.name};
    }) hostDefs.nixos
  );
in
{
  nixosSystem = mkNixosSystem;
  inherit allNixosConfigurations nixosConfigurations;
}
