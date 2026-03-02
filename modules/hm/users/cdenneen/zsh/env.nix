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
    fi
    if [ -r /run/secrets/github-token ]; then
      export GITHUB_TOKEN="$(tr -d '\n' </run/secrets/github-token)"
    elif [ -r /var/run/secrets/github-token ]; then
      export GITHUB_TOKEN="$(tr -d '\n' </var/run/secrets/github-token)"
    fi
    openclaw_token_file="''${OPENCLAW_GATEWAY_TOKEN_FILE:-$HOME/.config/openclaw/gateway.token}"
    if [ -r "$openclaw_token_file" ]; then
      export OPENCLAW_GATEWAY_TOKEN="$(tr -d '\n' <"$openclaw_token_file")"
    fi
    export OPENCLAW_BUNDLED_PLUGINS_DIR="$HOME/.openclaw/extensions"
  ''
]
