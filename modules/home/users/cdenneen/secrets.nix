{
  config,
  lib,
  pkgs,
  ...
}:
{
  home.file = lib.mkMerge [
    {
      ".local/bin/update-secrets" = {
        source = ./files/update-secrets;
        executable = true;
      };

      ".local/bin/restore-age-key" = {
        source = ./files/restore-age-key;
        executable = true;
      };
    }

    (lib.mkIf pkgs.stdenv.isDarwin {
      ".config/sops" = {
        source = config.lib.file.mkOutOfStoreSymlink "${config.home.homeDirectory}/Library/Application Support/sops";
        force = true;
      };

      ".config/sops-nix/secrets" = {
        source = config.lib.file.mkOutOfStoreSymlink "${config.home.homeDirectory}/Library/Application Support/sops-nix/secrets";
        force = true;
      };
    })
  ];
}
