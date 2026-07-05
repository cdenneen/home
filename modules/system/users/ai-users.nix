{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.userPresets;
in
{
  options.userPresets = {
    aiUsers = {
      enable = lib.mkEnableOption "AI users (claude, codex, opencode)";
    };
  };

  config = lib.mkIf cfg.aiUsers.enable {
    users.groups.aiusers = lib.mkIf pkgs.stdenv.isLinux { };

    users.users = {
      claude = {
        isSystemUser = true;
        group = "aiusers";
        home = "/var/lib/claude";
        createHome = true;
        description = "Claude AI user";
        shell = pkgs.zsh;
      };

      codex = {
        isSystemUser = true;
        group = "aiusers";
        home = "/var/lib/codex";
        createHome = true;
        description = "Codex AI user";
        shell = pkgs.zsh;
      };

      opencode = {
        isSystemUser = true;
        group = "aiusers";
        home = "/var/lib/opencode";
        createHome = true;
        description = "OpenCode AI user";
        shell = pkgs.zsh;
      };
    };

    nix.settings.trusted-users = [
      "claude"
      "codex"
      "opencode"
    ];
  };
}
