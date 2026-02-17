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

  homeConfiguration = mkHomeConfiguration;

  users = [ "cdenneen" ];

  homeConfigurations = builtins.listToAttrs (
    map (username: {
      name = username;
      value = homeConfiguration {
        system =
          if builtins ? currentSystem then
            builtins.currentSystem
          else
            let
              s = builtins.getEnv "NIX_SYSTEM";
            in
            if s != "" then s else throw "homeConfigurations: set NIX_SYSTEM or use --impure";
        homeModules = [ (defaultHomeModule username) ];
      };
    }) users
  );
in
{
  inherit homeConfigurations homeConfiguration;
}
