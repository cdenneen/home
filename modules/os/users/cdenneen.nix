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
                cfg.cdenneen.name
              ];
              #initialHashedPassword = "$y$j9T$3qj7b7.lXJ2wiK29g9njQ1$Dn.dhmjQvPSkmdtHbA.2qEDl3eUnMeaawAW84X0/5i0";
            })
          ]
        );
      };
      groups.${cfg.cdenneen.name} = lib.mkIf pkgs.stdenv.isLinux { };
    };
    home-manager.users.${cfg.cdenneen.name} = lib.mkIf cfg.cdenneen.enable {
      home.username = cfg.cdenneen.name;
      home.homeDirectory = "${homePath}/${cfg.cdenneen.name}";
      profiles = {
        cdenneen.enable = true;
        defaults.enable = true;
        #gui.enable = enableGui;
      };
    };
  };
}
