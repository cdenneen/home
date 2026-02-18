{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.zsh;
in
{
  config = lib.mkIf cfg.enable {
    programs.zsh = {
      autosuggestion.enable = true;
      syntaxHighlighting.enable = true;
      # Use Emacs keybindings so Ctrl-A / Ctrl-E work as expected
      defaultKeymap = "emacs";
      initContent = builtins.readFile ./zsh/init.zsh;
      envExtra = ''
        if [ -e $HOME/.nix-profile/etc/profile.d/nix.sh ]; then . $HOME/.nix-profile/etc/profile.d/nix.sh; fi
        if [ -e $HOME/.nix-profile/etc/profile.d/hm-session-vars.sh ]; then . $HOME/.nix-profile/etc/profile.d/hm-session-vars.sh; fi

        if [ -z "''${SOPS_AGE_KEY_FILE:-}" ]; then
          if [ -r /var/sops/age/keys.txt ]; then
            export SOPS_AGE_KEY_FILE=/var/sops/age/keys.txt
          elif [ -r "$HOME/.config/sops/age/keys.txt" ]; then
            export SOPS_AGE_KEY_FILE="$HOME/.config/sops/age/keys.txt"
          fi
        fi

        nix_paths=("${lib.concatStringsSep "\" \"" config.home.sessionPath}")
        IFS=':'
        setopt sh_word_split
        pre_paths=(/run/wrappers/bin $PATH)
        unsetopt sh_word_split
        paths_to_export=()
        for path in "''${pre_paths[@]}"; do
            if [[ -d "$path" && ! ''${nix_paths[(r)$path]} ]]; then
                paths_to_export+=("$path")
            fi
        done
        for path in "''${nix_paths[@]}"; do
            if [[ -d "$path" ]]; then
                paths_to_export+=("$path")
            fi
        done
        export PATH="''${paths_to_export[*]}"
        unset IFS
      '';
    };
    home.file.".hushlogin" = lib.mkIf pkgs.stdenv.isDarwin {
      text = "";
    };
  };
}
