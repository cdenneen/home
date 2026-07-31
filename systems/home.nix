{
  inputs,
  self,
  lib,
  hostCatalog ? import ../hosts,
}:
let
  inherit (lib) mkHomeConfiguration;

  defaultHomeModule =
    { pkgs, ... }:
    {
      home.username = "cdenneen";
      home.homeDirectory = if pkgs.stdenv.isDarwin then "/Users/cdenneen" else "/home/cdenneen";
      profiles.defaults.enable = true;
      profiles.gui.enable = pkgs.stdenv.isDarwin;
    };

  homeConfiguration = mkHomeConfiguration;

  allHosts = builtins.attrValues hostCatalog.allByName;

  homeConfigurations = builtins.listToAttrs (
    map (host: {
      name = "cdenneen@${host.name}";
      value = homeConfiguration {
        system = host.system;
        legacyBigSur = host.legacyBigSur or false;
        homeModules = [
          defaultHomeModule
          # Make the host name available during pure HM eval.
          (
            { ... }:
            {
              _module.args.nixHostName = host.name;
            }
          )
        ]
        ++ (host.homeModules or [ ]);
      };
    }) allHosts
  );
in
{
  inherit homeConfigurations homeConfiguration;
}
