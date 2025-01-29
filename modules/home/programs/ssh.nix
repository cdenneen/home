{ config, lib, pkgs, ... }:
let
  cfg = config.programs.ssh;
in
{
  config = lib.mkIf cfg.enable {
    home.packages = with pkgs; [
      ssm-session-manager-plugin
    ];
    programs = {
      ssh = {
        serverAliveInterval = 60;
        extraConfig = ''
          # Use SSHFP DNS records for host key verification
          VerifyHostKeyDNS yes
        '';
      };
    };
  };
}
