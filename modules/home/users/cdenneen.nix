{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.profiles;
in
{
  options.profiles.cdenneen.enable = lib.mkEnableOption "Enable cdenneen profile";

  config = lib.mkIf cfg.cdenneen.enable {
    home.sessionVariables.EDITOR = "nvim";
    programs = {
      alacritty.enable = cfg.gui.enable;
      direnv = {
        enable = true;
        nix-direnv.enable = true;
      };
      vscode.enable = cfg.gui.enable;
      wezterm.enable = cfg.gui.enable;
      kitty.enable = cfg.gui.enable;
      git = {
        enable = true;
        signing.signByDefault = true;
        userName = "Chris Denneen";
        userEmail = "cdenneen@gmail.com";
        signing.key = config.sops.secrets."gpg_gmail".path;
      };
      gpg = {
        enable = true;
        publicKeys = [

        ];
      };
    };
    home.packages =
      with pkgs;
      lib.optionals config.profiles.gui.enable [
        spotify
        discord
      ];
    catppuccin = {
      flavor = "latte";
      accent = "pink";
    };
    sops.secrets = {
      "fortress_rsa" = {
        path = "${config.home.homeDirectory}/.ssh/id_rsa.fortress";
        mode = "0400";
      };
      "cdenneen_ed25519_2024" = {
        path = "${config.home.homeDirectory}/.ssh/cdenneen_ed25519_2024.pem";
        mode = "0400";
      };
      "codecommit_rsa" = {
        path = "${config.home.homeDirectory}/.ssh/codecommit_rsa";
        mode = "0400";
      };
      "id_rsa_cloud9" = {
        path = "${config.home.homeDirectory}/.ssh/id_rsa_cloud9";
        mode = "0400";
      };
      "aws-config" = {
        path = "${config.home.homeDirectory}/.aws/config";
        mode = "0440";
      };
      "gpg_gmail" = {
        path = "${config.home.homeDirectory}/.gnupg/private-keys-v1.d/personal.key";
        mode = "0400";
      };
      "gpg_ap" = {
        path = "${config.home.homeDirectory}/.gnupg/private-keys-v1.d/work.key";
        mode = "0400";
      };
    };
  
  };
}
