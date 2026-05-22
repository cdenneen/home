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

  opencodeHomeModule =
    { pkgs, ... }:
    {
      programs.opencode.package = pkgs.callPackage ../pkgs/opencode-cli.nix { };
    };

  homeConfiguration = mkHomeConfiguration;

  allHosts = builtins.attrValues hostCatalog.allByName;

  extraModulesForHost = hostName: if hostName == "nyx" then [ ../hosts/nixos/nyx-home.nix ] else [ ];

  homeConfigurations = builtins.listToAttrs (
    map (host: {
      name = "cdenneen@${host.name}";
      value = homeConfiguration {
        system = host.system;
        homeModules = [
          defaultHomeModule
          opencodeHomeModule
        ]
        ++ extraModulesForHost host.name;
      };
    }) allHosts
  );
in
{
  inherit homeConfigurations homeConfiguration;
}
