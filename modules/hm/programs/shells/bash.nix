{ config, lib, ... }:
let
  cfg = config.programs.bash;
in
{
  config = lib.mkIf cfg.enable {
    programs.bash = {
      initExtra = ''
        set -o vi
        set completion-ignore-case on
      '';
      profileExtra = ''
        if [ -e $HOME/.nix-profile/etc/profile.d/nix.sh ]; then . $HOME/.nix-profile/etc/profile.d/nix.sh; fi
        if [ -r "/etc/profiles/per-user/$USER/etc/profile.d/hm-session-vars.sh" ]; then
          . "/etc/profiles/per-user/$USER/etc/profile.d/hm-session-vars.sh"
        elif [ -e $HOME/.nix-profile/etc/profile.d/hm-session-vars.sh ]; then
          . $HOME/.nix-profile/etc/profile.d/hm-session-vars.sh
        fi

        if [ -r "$HOME/.secrets" ]; then
          . "$HOME/.secrets"
          unset GITLAB_TOKEN GITLAB_ACCESS_TOKEN GITLAB_PERSONAL_ACCESS_TOKEN OAUTH_TOKEN 2>/dev/null || true
        fi

        if [ -r /run/secrets/github-token ]; then
          export GITHUB_TOKEN="$(tr -d '\n' </run/secrets/github-token)"
        elif [ -r /var/run/secrets/github-token ]; then
          export GITHUB_TOKEN="$(tr -d '\n' </var/run/secrets/github-token)"
        elif [ -r "$HOME/.local/share/sops-nix/secrets/github-token" ]; then
          export GITHUB_TOKEN="$(tr -d '\n' <"$HOME/.local/share/sops-nix/secrets/github-token")"
        elif [ -r "$HOME/.config/sops-nix/secrets/github-token" ]; then
          export GITHUB_TOKEN="$(tr -d '\n' <"$HOME/.config/sops-nix/secrets/github-token")"
        fi

        if [ -z "''${SOPS_AGE_KEY_FILE:-}" ]; then
          if [ -r /var/sops/age/keys.txt ]; then
            export SOPS_AGE_KEY_FILE=/var/sops/age/keys.txt
          elif [ -r "$HOME/.config/sops/age/keys.txt" ]; then
            export SOPS_AGE_KEY_FILE="$HOME/.config/sops/age/keys.txt"
          fi
        fi

        nix_paths=("${lib.concatStringsSep "\" \"" config.home.sessionPath}")
        IFS=':'
        read -r -a pre_paths <<< "/run/wrappers/bin:$PATH"
        paths_to_export=()
        for path in "''${pre_paths[@]}"; do
            if [[ -d "$path" && ! " ''${nix_paths[@]} " =~ " ''${path} " ]]; then
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
  };
}
