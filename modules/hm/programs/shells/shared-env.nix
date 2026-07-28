{ lib }:
{
  commonBootstrap = ''
    if [ -z "''${XDG_RUNTIME_DIR:-}" ] && command -v id >/dev/null 2>&1; then
      runtime_dir="/run/user/$(id -u)"
      if [ -d "$runtime_dir" ]; then
        export XDG_RUNTIME_DIR="$runtime_dir"
      fi
      unset runtime_dir
    fi

    if [ -r "/etc/profiles/per-user/$USER/etc/profile.d/hm-session-vars.sh" ]; then
      . "/etc/profiles/per-user/$USER/etc/profile.d/hm-session-vars.sh"
    elif [ -r "$HOME/.nix-profile/etc/profile.d/hm-session-vars.sh" ]; then
      . "$HOME/.nix-profile/etc/profile.d/hm-session-vars.sh"
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

    if [ -n "''${XDG_RUNTIME_DIR:-}" ] && [ -S "$XDG_RUNTIME_DIR/ssh-agent" ]; then
      export SSH_AUTH_SOCK="$XDG_RUNTIME_DIR/ssh-agent"
    fi
  '';

  bashPathBootstrap = sessionPath: ''
    nix_paths=("${lib.concatStringsSep "\" \"" sessionPath}")
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

  zshPathBootstrap = sessionPath: ''
    nix_paths=("${lib.concatStringsSep "\" \"" sessionPath}")
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
}
