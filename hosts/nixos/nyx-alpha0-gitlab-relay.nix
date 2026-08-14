{
  lib,
  pkgs,
  ...
}:
{
  users.groups.alpha0-gitlab-relay = { };
  users.users.alpha0-gitlab-relay = {
    isSystemUser = true;
    group = "alpha0-gitlab-relay";
    home = "/var/empty/alpha0-gitlab-relay";
    shell = "${pkgs.shadow}/bin/nologin";
    openssh.authorizedKeys.keys = [
      ''restrict,port-forwarding,permitopen="git.ap.org:443" ${builtins.readFile ../../pub/ssh/alpha0-gitlab-relay-ghost.pub}''
    ];
  };

  services.openssh.extraConfig = lib.mkAfter ''
    Match User alpha0-gitlab-relay
      AuthenticationMethods publickey
      PasswordAuthentication no
      KbdInteractiveAuthentication no
      PermitTTY no
      AllowAgentForwarding no
      AllowTcpForwarding local
      AllowStreamLocalForwarding no
      X11Forwarding no
      PermitTunnel no
      PermitUserRC no
      PermitOpen git.ap.org:443
      PermitListen none
      GatewayPorts no
      MaxSessions 0
    Match all
  '';
}
