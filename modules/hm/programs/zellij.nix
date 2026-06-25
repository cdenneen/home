{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.zellij;
in
{
  options.programs.zellij.restrictedVariables = lib.mkOption {
    description = "List of environment variables that will prevent zellij from starting if they are set. This is useful for preventing zellij from starting when it is not desired, such as when used as the shell within various ides.";
    default = {
      TERM = [ "xterm-ghostty" ];
      TERMINAL_EMULATOR = [ "JetBrains-JediTerm" ];
      TERM_PROGRAM = [
        "vscode"
        "WarpTerminal"
      ];
      ZED_TERM = [ "true" ];
    };
    type = lib.types.attrsOf (lib.types.listOf lib.types.str);
  };
  config = lib.mkIf cfg.enable {
    programs.zellij = {
      settings = {
        theme = "catppuccin-${config.catppuccin.flavor}";
      };
      enableFishIntegration = lib.mkForce false;
      enableBashIntegration = lib.mkForce false;
      enableZshIntegration = lib.mkForce false;
    };
    home.shellAliases = {
      zellij-bash = "${lib.getExe pkgs.zellij} options --default-shell ${lib.getExe pkgs.bashInteractive} --session-name bash --attach-to-session true";
      zellij-zsh = "${lib.getExe pkgs.zellij} options --default-shell ${lib.getExe pkgs.zsh} --session-name zsh --attach-to-session true";
    };
    programs = {
      # extension of zellij setup --generate-auto-start $SHELL because the code generated doesn't start zellij with a window with the shell that started it, but the default shell of the user. Additionally generation doesn't support nushell
      # Don't auto start zellij within zellij, when connected via ssh, or when used as the shell within ides
      bash.initExtra = ''
        if [ -z "$ZELLIJ" ] && [ -z "$SSH_CONNECTION" ] && ${
          lib.concatStringsSep " && " (
            lib.mapAttrsToList (
              name: values: lib.concatStringsSep " && " (map (v: "[ \"\$${name}\" != \"${v}\" ]") values)
            ) cfg.restrictedVariables
          )
        }; then
          zellij-bash
        fi
      '';
      zsh.initContent = ''
        if [ -z "$ZELLIJ" ] && [ -z "$SSH_CONNECTION" ] && ${
          lib.concatStringsSep " && " (
            lib.mapAttrsToList (
              name: values: lib.concatStringsSep " && " (map (v: "[ \"\$${name}\" != \"${v}\" ]") values)
            ) cfg.restrictedVariables
          )
        }; then
          zellij-zsh
        fi
      '';
    };
  };
}
