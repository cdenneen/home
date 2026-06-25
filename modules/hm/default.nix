{
  pkgs,
  config,
  lib,
  osConfig ? null,
  ...
}:
let
  hostKeyFile = "/var/sops/age/keys.txt";
in
{
  imports = [
    ./users
    ./programs
  ];

  config = {
    sops = {
      defaultSopsFile = ../../secrets/secrets.yaml;
      age = {
        keyFile =
          if pkgs.stdenv.isLinux then
            hostKeyFile
          else
            "${config.home.homeDirectory}/Library/Application Support/sops/age/keys.txt";
      };
    };
    home.packages = lib.optionals (config.launchd.agents ? sops-nix) [
      (pkgs.writeShellScriptBin "sops-nix-user" "${config.launchd.agents.sops-nix.config.Program}")
    ];
  };
}
