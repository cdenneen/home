{
  lib,
  pkgs,
  ...
}:
let
  alpha0Node = pkgs.callPackage ../../pkgs/alpha0-node.nix { };
  stateDir = "/var/lib/alpha0-node";
  repositorySources = {
    eks-platform-governance = "/home/cdenneen/src/cache/git.ap.org_gitops_infra_eks-platform_eks-platform-governance.git";
    gitlab-governance = "/home/cdenneen/src/cache/git.ap.org_gitops_infra_gitlab_gitlab-governance.git";
  };
  repositories = lib.mapAttrs (name: _: "${stateDir}/repositories/${name}") repositorySources;
in
{
  users.groups.alpha0-node = { };
  users.users.alpha0-node = {
    isSystemUser = true;
    group = "alpha0-node";
    home = stateDir;
    shell = pkgs.bashInteractive;
    openssh.authorizedKeys.keyFiles = [ ../../pub/ssh/alpha0-node-ghost.pub ];
  };

  services.openssh.extraConfig = lib.mkAfter ''
    Match User alpha0-node
      AuthenticationMethods publickey
      PasswordAuthentication no
      KbdInteractiveAuthentication no
      PermitTTY no
      DisableForwarding yes
      AllowAgentForwarding no
      AllowTcpForwarding no
      AllowStreamLocalForwarding no
      X11Forwarding no
      PermitTunnel no
      PermitOpen none
      PermitListen none
      GatewayPorts no
      MaxSessions 1
      ForceCommand /run/current-system/sw/bin/alpha0-node --config /etc/alpha0-node/config.json
    Match all
  '';

  environment.systemPackages = [ alpha0Node ];
  environment.etc."alpha0-node/config.json" = {
    mode = "0444";
    text = builtins.toJSON {
      node_id = "nyx";
      inherit repositories;
      capabilities = [ "git.ap.org" ];
      max_concurrent = 2;
      state_dir = stateDir;
      workers.inspect = [ "${alpha0Node}/bin/alpha0-node-inspect" ];
      # Add only dedicated node-local profile names after their roles are reviewed.
      aws_profiles = [ ];
    };
  };

  fileSystems = lib.mapAttrs' (
    name: source:
    lib.nameValuePair repositories.${name} {
      device = source;
      fsType = "none";
      options = [
        "bind"
        "ro"
        "nodev"
        "nosuid"
        "noexec"
        "nofail"
      ];
    }
  ) repositorySources;

  systemd.tmpfiles.rules = [
    "d ${stateDir} 0700 alpha0-node alpha0-node -"
    "d ${stateDir}/locks 0700 alpha0-node alpha0-node -"
    "d ${stateDir}/packages 0700 alpha0-node alpha0-node -"
    "d ${stateDir}/repositories 0700 alpha0-node alpha0-node -"
    "d ${stateDir}/repositories/eks-platform-governance 0550 alpha0-node alpha0-node -"
    "d ${stateDir}/repositories/gitlab-governance 0550 alpha0-node alpha0-node -"
    "d ${stateDir}/slots 0700 alpha0-node alpha0-node -"
  ];
}
