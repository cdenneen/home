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
      history = {
        append = true;
        expireDuplicatesFirst = true;
        extended = true;
        ignoreAllDups = true;
        ignoreDups = true;
        ignoreSpace = true;
        path = "${config.home.homeDirectory}/.local/state/zsh/history";
        save = 100000;
        share = true;
        size = 130000;
      };
      sessionVariables =
        {
          # This is required for the zoxide integration
          ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE = "fg=8";
        }
        // (
          if pkgs.stdenv.isDarwin then
            {
              XDG_RUNTIME_DIR = "$(getconf DARWIN_USER_TEMP_DIR)";
            }
          else
            { }
        );

      defaultKeymap = "viins";
      initContent = ''
                # Amazon Q pre block. Keep at the top of this file.
                [[ -f "$${HOME}/Library/Application Support/amazon-q/shell/zshrc.pre.zsh" ]] && builtin source "$${HOME}/Library/Application Support/amazon-q/shell/zshrc.pre.zsh"
                setopt globdots
                zstyle ':completion:*' matcher-list ''' '+m:{a-zA-Z}={A-Za-z}' '+r:|[._-]=* r:|=*' '+l:|=* r:|=*'
                if [[ -z "$SSH_AUTH_SOCK" ]]; then
                  export SSH_AUTH_SOCK="${config.programs.gpg.package}/bin/gpgconf --list-dirs agent-ssh-socket"
                fi
                bindkey -e

                bindkey '^[w' kill-region

                zle_highlight+=(paste:none)

                #█▓▒░ load configs
                for config (~/.config/zsh/*.zsh) source $config

                # Determine the target file of the symlink
                secrets_target=$(readlink ~/.secrets || echo ~/.secrets)

                # Check if the target exists and its modification time
                if [ ! -e "$secrets_target" ] || [ -n "$(find "$secrets_target" -mtime +7 2>/dev/null)" ]; then
                  update_secrets
                fi

                source <(switcher init zsh)
                source <(switch completion zsh)

                source ~/.secrets
        	# Amazon Q post block. Keep at the bottom of this file.
                [[ -f "$${HOME}/Library/Application Support/amazon-q/shell/zshrc.post.zsh" ]] && builtin source "$${HOME}/Library/Application Support/amazon-q/shell/zshrc.post.zsh"
      '';
      plugins = [
        {
          # will source zsh-autosuggestions.plugin.zsh
          name = "zsh-autosuggestions";
          src = pkgs.zsh-autosuggestions;
          file = "share/zsh-autosuggestions/zsh-autosuggestions.zsh";
        }
        {
          name = "zsh-completions";
          src = pkgs.zsh-completions;
          file = "share/zsh-completions/zsh-completions.zsh";
        }
        {
          name = "zsh-syntax-highlighting";
          src = pkgs.zsh-syntax-highlighting;
          file = "share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh";
        }
        # {
        #   name = "powerlevel10k";
        #   src = pkgs.zsh-powerlevel10k;
        #   file = "share/zsh-powerlevel10k/powerlevel10k.zsh-theme";
        # }
        {
          name = "fzf-tab";
          src = pkgs.zsh-fzf-tab;
          file = "share/fzf-tab/fzf-tab.plugin.zsh";
        }
      ];
      envExtra = ''
        if [ -e $HOME/.nix-profile/etc/profile.d/nix.sh ]; then . $HOME/.nix-profile/etc/profile.d/nix.sh; fi
        if [ -e $HOME/.nix-profile/etc/profile.d/hm-session-vars.sh ]; then . $HOME/.nix-profile/etc/profile.d/hm-session-vars.sh; fi

        nix_paths=("${lib.concatStringsSep "\" \"" config.home.sessionPath}")
        IFS=':'
        setopt sh_word_split
        pre_paths=($PATH)
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
