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
    if [ -r "$HOME/.secrets" ]; then
      source "$HOME/.secrets"
    fi
    if [ -r /run/secrets/github-token ]; then
      export GITHUB_TOKEN="$(tr -d '\n' </run/secrets/github-token)"
    elif [ -r /var/run/secrets/github-token ]; then
      export GITHUB_TOKEN="$(tr -d '\n' </var/run/secrets/github-token)"
    fi
  ''
]
