{
  alpha0,
  axis-control,
  hermes-src,
  pkgs,
  agentPkgs,
  ...
}:
let
  # G-CONT item 1 (2026-08-31): hermes-alpha0-gateway.service is a legacy,
  # unmanaged artifact - its definer (docs/recovery/legacy-alpha0-gateway/
  # home-manager-module.nix) is not imported by any currently-active
  # config, and flake.nix explicitly asserts `!(services ?
  # hermes-alpha0-gateway)` - reintroducing a Nix-declared
  # systemd.user.services.hermes-alpha0-gateway would violate that
  # invariant (and is what produced a replacement unit instead of a merge
  # when tried: there was nothing else to merge with). Extending via a
  # systemd drop-in file instead - systemd's own native mechanism for
  # adding to a unit without owning/replacing its base file. A disabled
  # drop-in with this exact shape already exists on-disk
  # (hermes-alpha0-gateway.service.d/override.conf.pre-home-manager),
  # confirming this is the established mechanism for this unit, not a new
  # pattern.
  workloadMetadata = import ../../modules/hm/users/cdenneen/hermes-workload-metadata { inherit pkgs agentPkgs; };
  governorClassification = import ../../modules/hm/users/cdenneen/hermes-governor-classification { inherit pkgs agentPkgs; };
  hermesAlpha0GatewaySitecustomize = workloadMetadata.mkCombinedSitecustomize governorClassification.governorClassificationPy;
in
{
  imports = [ alpha0.homeModules.default ];

  xdg.configFile."systemd/user/hermes-alpha0-gateway.service.d/99-gcont-governor-classification.conf".text = ''
    [Service]
    Environment=PYTHONPATH=${hermesAlpha0GatewaySitecustomize}
    ExecStartPre=${governorClassification.selftestCheck}
  '';

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

  # Canonical Alpha0 is imported but remains fully dormant. Core and gateway
  # graduate independently after the recovery gates are satisfied.
  services.alpha0 = {
    enableCore = false;
    enableGateway = false;
    package = alpha0.packages.${pkgs.stdenv.hostPlatform.system}.default;
    hermesPackage = hermes-src.packages.${pkgs.stdenv.hostPlatform.system}.messaging;
  };

  profiles.hermesAxisControlGateway.enable = false;
  profiles.hermesGateway = {
    enable = true;
    environmentFile = "%h/.hermes/.env";
  };
  profiles.hermesSlackPlatformOverride.targets = {
    alpha0.homeRelativePath = ".local/share/alpha0/hermes";
    canonical-axis-control.homeRelativePath = ".hermes/profiles/axis-control";
    # The disabled dedicated gateway remains live in Ghost's older deployed
    # generation. Keep its profile patched until that stale unit is retired.
    legacy-axis-control.homeRelativePath = "src/workspace/work/axis-control/.hermes/profiles/axis-control";
  };
  # The recovered local supervisor remains forensic/decommissioning evidence,
  # not Ghost's AXIS application authority.
  profiles.hermesSupervisor.enable = false;

  services.axis-control-observer = {
    enable = true;
    package = axis-control.packages.${pkgs.stdenv.hostPlatform.system}.default;
  };

  # This only deploys the canonical profile wrapper and report-only watchdog.
  # Installing or graduating the Hermes scheduler is a separate reviewed step.

  # Bootstrap-tier host-local policy endpoints (deployed, healthy, NOT yet
  # referenced by any Hermes profile's model.base_url - see
  # modules/hm/users/cdenneen/hermes-policy-endpoint and
  # bootstrap-gate-evidence.md). Wiring a profile to actually use one is a
  # separate Phase 2 canary action requiring its own explicit approval.
  # trust_domain/agent/workstream are a first-class dimension independent
  # of gateway/profile identity (new architecture constraint): Ghost is
  # uniformly the PERSONAL trust domain (even axis-control, which does
  # software-engineering work, mutates only this personal repo, not an
  # employer system) - contrast Nyx below, which is the WORK trust domain.
  #
  # continuityClass here is a CEILING only (continuity-class-audit.md):
  # the most permissive class any workload on this gateway could ever
  # reach, set to the most-permissive value appearing anywhere in the
  # audit's per-workload table for this gateway. It is deliberately NOT
  # the old blanket per-gateway value (that was the audit's finding -
  # unapproved, superseded). action_classification.py's per-request
  # tool/source classification takes the more restrictive of (its own
  # classification, this ceiling) for every actual request - this ceiling
  # never grants more than automatic-read-only on its own.
  profiles.hermesPolicyEndpoint.instances = {
    ghost-default = {
      port = 8601;
      trustDomain = "personal";
      agent = "ghost";
      workstream = "assistant";
      priority = "P1";
      continuityClass = "automatic-read-only";
      erosBaseUrl = "http://eros.tail0e55.ts.net:4000";
      erosTailscaleIp = "100.117.68.38";
      # #41: mirrors this key's real LiteLLM allowlist exactly (confirmed
      # live via /key/info, 2026-08-27) - update both together if ever changed.
      allowedRoutes = [ "tier0-local" "tier1-general" "tier1-coding" "tier2-general" "tier2-coding" "tier2-research" "tier3-quality" ];
      erosApiKeySecret = "eros_litellm_key_ghost_default";
    };
    ghost-alpha0 = {
      port = 8602;
      trustDomain = "personal";
      agent = "ghost";
      workstream = "alpha0";
      priority = "P1";
      continuityClass = "automatic-read-only";
      erosBaseUrl = "http://eros.tail0e55.ts.net:4000";
      erosTailscaleIp = "100.117.68.38";
      allowedRoutes = [ "tier2-research" "tier3-quality" "mini" ];
      erosApiKeySecret = "eros_litellm_key_ghost_alpha0";
    };
    ghost-axis-control = {
      port = 8603;
      trustDomain = "personal";
      agent = "ghost";
      workstream = "axis-control";
      priority = "P1";
      continuityClass = "automatic-read-only";
      erosBaseUrl = "http://eros.tail0e55.ts.net:4000";
      erosTailscaleIp = "100.117.68.38";
      allowedRoutes = [ "tier2-coding" "tier2-general" "tier3-quality" ];
      erosApiKeySecret = "eros_litellm_key_ghost_axis_control";
    };
  };

  # auto/mini/quality migration (2026-09-02): ghost-alpha0's auxiliary
  # functions move off the old tier2-research name onto the consolidated
  # mini combo (#747). model.default is deliberately left unpinned here -
  # it stays on tier4-frontier, an explicit-only frontier grant, not part
  # of this migration.
  profiles.hermesProfileModel.profiles.ghost-alpha0 = {
    configHomeRelativePath = ".local/share/alpha0/hermes/profiles/alpha0/config.yaml";
    modelOverrides = {
      "auxiliary.compression.model" = "mini";
      "auxiliary.title_generation.model" = "mini";
    };
  };
}
