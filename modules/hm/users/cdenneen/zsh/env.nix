{
  pkgs,
  lib,
  sessionPath,
}:

let
  sharedEnv = import ../../../programs/shells/shared-env.nix { inherit lib; };
in

lib.concatStringsSep "\n" [
  (lib.optionalString pkgs.stdenv.hostPlatform.isDarwin ''
    export PATH="/run/wrappers/bin:/run/current-system/sw/bin:$HOME/.nix-profile/bin:$PATH"
    if [ -r /etc/zprofile ]; then
      source /etc/zprofile
    fi
  '')
  (lib.optionalString pkgs.stdenv.hostPlatform.isLinux ''
    if [ -r /etc/profile ]; then
      source /etc/profile
    fi
    export PATH="/run/wrappers/bin:$PATH"
  '')
  sharedEnv.commonBootstrap
  (sharedEnv.zshPathBootstrap sessionPath)
]
