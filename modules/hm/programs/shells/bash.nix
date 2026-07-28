{ config, lib, ... }:
let
  cfg = config.programs.bash;
  sharedEnv = import ./shared-env.nix { inherit lib; };
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
        ${sharedEnv.commonBootstrap}
        ${sharedEnv.bashPathBootstrap config.home.sessionPath}
      '';
    };
  };
}
