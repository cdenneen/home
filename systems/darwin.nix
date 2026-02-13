{
  inputs,
  self,
  lib,
}:
let
  inherit (lib) mkDarwinSystem;

  hostDefs = import ../hosts;

  darwinConfigurations = builtins.listToAttrs (
    map (host: {
      name = host.name;
      value = mkDarwinSystem {
        system = host.system;
        darwinModules = host.modules;
      };
    }) hostDefs.darwin
  );
in
{
  darwinSystem = mkDarwinSystem;
  inherit darwinConfigurations;
}
