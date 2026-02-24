{
  inputs,
  self,
  lib,
}:
let
  inherit (lib) mkHomeConfiguration;

  defaultHomeModule =
    username:
    { pkgs, ... }:
    {
      home.username = username;
      home.homeDirectory = if pkgs.stdenv.isDarwin then "/Users/${username}" else "/home/${username}";
      profiles.defaults.enable = true;
      profiles.gui.enable = pkgs.stdenv.isDarwin;
    };

  opencodeHomeModule =
    { pkgs, ... }:
    {
      programs.opencode.package = inputs.opencode.packages.${pkgs.stdenv.hostPlatform.system}.default;
    };

  homeConfiguration = mkHomeConfiguration;

  users = [ "cdenneen" ];

  hostDefs = import ../hosts;
  allHosts = hostDefs.nixos ++ hostDefs.darwin;

  extraModulesForHost = hostName: if hostName == "nyx" then [ ../hosts/nixos/nyx-home.nix ] else [ ];

  homeConfigurations = builtins.listToAttrs (
    builtins.concatLists (
      map (
        host:
        (map (username: {
          name = "${username}@${host.name}";
          value = homeConfiguration {
            system = host.system;
            homeModules = [
              (defaultHomeModule username)
              opencodeHomeModule
            ]
            ++ extraModulesForHost host.name;
          };
        }) users)
      ) allHosts
    )
  );
in
{
  inherit homeConfigurations homeConfiguration;
}
