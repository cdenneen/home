{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.userPresets;
  homePath = if pkgs.stdenv.isDarwin then "/Users" else "/home";
  enableGui = config.profiles.gui.enable;
in
{
  options.userPresets = {
    cdenneen = {
      enable = lib.mkEnableOption "cdenneen user";
      name = lib.mkOption {
        type = lib.types.str;
        default = "cdenneen";
      };
    };
  };

  config = {
    users = {
      users = {
        ${cfg.cdenneen.name} = lib.mkIf cfg.cdenneen.enable (
          lib.mkMerge [
            {
              name = cfg.cdenneen.name;
              description = "cdenneen Diekvoss";
              home = "${homePath}/${cfg.cdenneen.name}";
              shell = pkgs.zsh;
            }
            (lib.mkIf pkgs.stdenv.isLinux {
              isNormalUser = true;
              extraGroups = [
                "networkmanager"
                "wheel"
                "input"
                "uinput"
                cfg.cdenneen.name
              ] ++ lib.optionals config.containerPresets.podman.enable [ "podman" ];
              initialHashedPassword = "$y$j9T$8Gh6/.8Z.viwXCwRkvGFv.$LjcK6HYBvggZpp21Aiy0mt1UR9lRqlZ.PCVrXTpGT35";
            })
          ]
        );
      };
      groups.${cfg.cdenneen.name} = lib.mkIf pkgs.stdenv.isLinux { };
    };
    nix.settings.trusted-users = [
      cfg.cdenneen.name
      root
    ];
    home-manager.users.${cfg.cdenneen.name} = lib.mkIf cfg.cdenneen.enable {
      home.username = cfg.cdenneen.name;
      home.homeDirectory = "${homePath}/${cfg.cdenneen.name}";
      profiles = {
        defaults.enable = true;
        gui.enable = enableGui;
        cdenneen.enable = true;
      };
    };
  };
}
