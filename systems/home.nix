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
    {
      agentPkgs ? null,
      pkgs,
      ...
    }:
    {
      programs.opencode.package = agentPkgs.opencode;
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
          # Make the host name available during pure HM eval.
          (
            { ... }:
            {
              _module.args.nixHostName = host.name;
            }
          )
        ]
        ++ extraModulesForHost host.name;
      };
    }) allHosts
  );
in
{
  inherit homeConfigurations homeConfiguration;
}
