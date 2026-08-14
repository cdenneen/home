{ pkgs, ... }:
{
  systemd.user.startServices = false;

  # Manually operated until Alpha0's GitLab relay acceptance gate passes.
  systemd.user.services.alpha0-gitlab-nyx-relay = {
    Unit = {
      Description = "Alpha0 GitLab TLS relay through Nyx";
      After = [ "network-online.target" ];
      Wants = [ "network-online.target" ];
      StartLimitIntervalSec = 300;
      StartLimitBurst = 5;
    };
    Service = {
      Type = "simple";
      ExecStart = ''
        ${pkgs.openssh}/bin/ssh -NT \
          -L 127.0.0.1:19443:git.ap.org:443 \
          -o BatchMode=yes \
          -o ConnectTimeout=10 \
          -o ExitOnForwardFailure=yes \
          -o ForwardAgent=no \
          -o ServerAliveInterval=30 \
          -o ServerAliveCountMax=3 \
          -o StrictHostKeyChecking=yes \
          nyx
      '';
      Restart = "on-failure";
      RestartSec = 10;
      NoNewPrivileges = true;
      PrivateTmp = true;
    };
  };

  profiles.hermesAlpha0Gateway.enable = true;
  profiles.hermesGateway.enable = true;
  profiles.hermesSupervisor.enable = true;
}
