{
  config,
  lib,
  pkgs,
  ...
}:
let
  alpha0Node = pkgs.callPackage ../../pkgs/alpha0-node.nix { };
  stateDir = "/var/lib/alpha0-node";
  repositorySources = {
    cluster-bootstrap = "/home/cdenneen/src/cache/git.ap.org_gitops_infra_eks-platform_cluster-bootstrap.git";
    eks-platform-governance = "/home/cdenneen/src/cache/git.ap.org_gitops_infra_eks-platform_eks-platform-governance.git";
    fleet-v2 = "/home/cdenneen/src/cache/git.ap.org_gitops_infra_eks-platform_fleet-v2.git";
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
      capabilities = [
        "git.ap.org"
        "plan.read-only"
      ];
      max_concurrent = 2;
      state_dir = stateDir;
      workers.inspect = [ "${alpha0Node}/bin/alpha0-node-inspect" ];
      workers.codex-plan = [ "${alpha0Node}/bin/alpha0-node-codex-plan" ];
      worker_context_refs.inspect = [
        "repo://alpha0/skills/agent-tool-usage/SKILL.md#sha256:eb56e2626694a949e30af09879eae94536f113f881e3ee71ee0c5c918b519fc8"
        "repo://alpha0/skills/project-sdlc/SKILL.md#sha256:ff724c5d06b38dc002a8eccd46155198d39e001dc4ddd8b72346eb396aa20d23"
      ];
      worker_context_refs.codex-plan = [
        "repo://alpha0/skills/agent-tool-usage/SKILL.md#sha256:eb56e2626694a949e30af09879eae94536f113f881e3ee71ee0c5c918b519fc8"
        "repo://alpha0/skills/project-sdlc/SKILL.md#sha256:ff724c5d06b38dc002a8eccd46155198d39e001dc4ddd8b72346eb396aa20d23"
      ];
      worker_secret_files.inspect = { };
      worker_secret_files.codex-plan.OPENAI_API_KEY =
        config.sops.secrets."alpha0-node/openai-api-key".path;
      aws_cli = "${pkgs.awscli2}/bin/aws";
      # Add only dedicated node-local profile names after their roles are reviewed.
      aws_profiles = [ ];
    };
  };

  sops.secrets."alpha0-node/openai-api-key" = {
    key = "openai_api_key";
    owner = "alpha0-node";
    group = "alpha0-node";
    mode = "0400";
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
    "d ${stateDir}/auth 0700 alpha0-node alpha0-node -"
    "d ${stateDir}/locks 0700 alpha0-node alpha0-node -"
    "d ${stateDir}/packages 0700 alpha0-node alpha0-node -"
    "d ${stateDir}/repositories 0700 alpha0-node alpha0-node -"
    "d ${stateDir}/slots 0700 alpha0-node alpha0-node -"
  ]
  ++ lib.mapAttrsToList (
    name: _: "d ${repositories.${name}} 0550 alpha0-node alpha0-node -"
  ) repositorySources;
}
