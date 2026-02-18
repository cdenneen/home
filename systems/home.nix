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
        homeModules = [
          (defaultHomeModule username)
          opencodeHomeModule
        ];
      };
    }) users
  );
in
{
  inherit homeConfigurations homeConfiguration;
}
