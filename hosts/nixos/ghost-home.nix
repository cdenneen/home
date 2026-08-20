{ axis-control, pkgs, ... }:
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
        ${pkgs.coreutils}/bin/env -i \
          HOME=/var/empty \
          ${pkgs.openssh}/bin/ssh -F /dev/null -NT \
          -l alpha0-gitlab-relay \
          -i /run/secrets/alpha0/gitlab-nyx-relay-identity \
          -L 127.0.0.1:19443:git.ap.org:443 \
          -o BatchMode=yes \
          -o ConnectTimeout=10 \
          -o ExitOnForwardFailure=yes \
          -o ForwardAgent=no \
          -o IdentitiesOnly=yes \
          -o IdentityAgent=none \
          -o PermitLocalCommand=no \
          -o RequestTTY=no \
          -o ServerAliveInterval=30 \
          -o ServerAliveCountMax=3 \
          -o StrictHostKeyChecking=yes \
          -o UpdateHostKeys=no \
          -o UserKnownHostsFile=/etc/ssh/alpha0-node-nyx-known-hosts \
          100.80.58.4
      '';
      Restart = "on-failure";
      RestartSec = 10;
      NoNewPrivileges = true;
      PrivateTmp = true;
      UMask = "0077";
    };
  };

  profiles.hermesAlpha0Gateway.enable = true;
  profiles.hermesAxisControlGateway.enable = false;
  profiles.hermesGateway.enable = true;
  # The recovered local supervisor remains forensic/decommissioning evidence,
  # not Ghost's AXIS application authority.
  profiles.hermesSupervisor.enable = false;

  services.axis-control-observer = {
    enable = true;
    package = axis-control.packages.${pkgs.stdenv.hostPlatform.system}.default;
  };

  # This only deploys the canonical profile wrapper and report-only watchdog.
  # Installing or graduating the Hermes scheduler is a separate reviewed step.
}
