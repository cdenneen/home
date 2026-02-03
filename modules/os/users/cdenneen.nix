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

  config = lib.mkIf cfg.cdenneen.enable {
    users.users.${cfg.cdenneen.name} = lib.mkMerge [
      {
        name = cfg.cdenneen.name;
        description = "Chris Denneen";
        home = "${homePath}/${cfg.cdenneen.name}";
        shell = pkgs.zsh;
        packages = [
          pkgs._1password-cli
        ];
      }
      (lib.mkIf pkgs.stdenv.isLinux {
        isNormalUser = true;
        # Linux-only: nix-darwin does not manage user passwords
        initialHashedPassword = "$6$110Kl1BJUnU6QXEO$u7Ij2S63bEmwNj/J..rhKZ1kuBWs8/mPWwOMvDjoajuQPxUDcE8ld81RsC69lZGyHlogyajFU0V.nvJAeGx16.";
        extraGroups = [
          "networkmanager"
          "wheel"
          cfg.cdenneen.name
        ];
      })
    ];

    users.groups.${cfg.cdenneen.name} = lib.mkIf pkgs.stdenv.isLinux { };

    nix.settings.trusted-users = [ cfg.cdenneen.name ];

    home-manager.users.${cfg.cdenneen.name} = {
      home.username = cfg.cdenneen.name;
      home.homeDirectory = "${homePath}/${cfg.cdenneen.name}";
      imports = [
        ../../home/users/cdenneen/default.nix
      ];
      profiles = {
        defaults.enable = true;
        gui.enable = enableGui;
      };
    };
  };
}
