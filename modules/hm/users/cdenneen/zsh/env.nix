{ pkgs, lib }:

lib.concatStringsSep "\n" [
  (lib.optionalString pkgs.stdenv.isDarwin ''
    export PATH="/run/wrappers/bin:/run/current-system/sw/bin:$HOME/.nix-profile/bin:$PATH"
    if [ -r /etc/zprofile ]; then
      source /etc/zprofile
    fi
  '')
  (lib.optionalString pkgs.stdenv.isLinux ''
    if [ -r /etc/profile ]; then
      source /etc/profile
    fi
    export PATH="/run/wrappers/bin:$PATH"
  '')
  ''
    if [ -r "/etc/profiles/per-user/cdenneen/etc/profile.d/hm-session-vars.sh" ]; then
      source "/etc/profiles/per-user/cdenneen/etc/profile.d/hm-session-vars.sh"
    elif [ -r "/etc/profiles/per-user/$USER/etc/profile.d/hm-session-vars.sh" ]; then
      source "/etc/profiles/per-user/$USER/etc/profile.d/hm-session-vars.sh"
    elif [ -r "$HOME/.nix-profile/etc/profile.d/hm-session-vars.sh" ]; then
      source "$HOME/.nix-profile/etc/profile.d/hm-session-vars.sh"
    fi
    if [ -r "$HOME/.secrets" ]; then
      source "$HOME/.secrets"
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
  ''
]
