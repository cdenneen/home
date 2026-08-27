{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.profiles.hermesPolicyEndpoint;
  endpointSrc = ./.;
  stateRoot = "${config.home.homeDirectory}/.hermes-policy";

  mkInstance =
    name: inst:
    let
      instRoot = "${stateRoot}/${name}";
      # The rendered config only ever contains paths, never secret values -
      # sops-nix's own activation/runtime ordering is independent of home-
      # manager's activation scripts, so a secret's decrypted content is
      # not reliably readable during `home.activation` (confirmed live:
      # activation-time substitution raced sops-nix and failed). The
      # service reads eros_api_key_file itself at process startup instead.
      configFile = pkgs.writeText "hermes-policy-endpoint-${name}-config.json" (
        builtins.toJSON {
          actor = name;
          priority = inst.priority;
          continuity_class = inst.continuityClass;
          eros_base_url = inst.erosBaseUrl;
          eros_tailscale_ip = inst.erosTailscaleIp;
          eros_port = inst.erosPort;
          eros_api_key_file = config.sops.secrets.${inst.erosApiKeySecret}.path;
          monthly_budget_usd = inst.monthlyBudgetUsd;
          expected_burn_1h_usd = inst.expectedBurn1hUsd;
          state_db_path = "${instRoot}/state.db";
          # Deliberately not yet provisioned - see continuity.py /
          # bootstrap-gate-evidence.md. This path intentionally does not
          # exist until an emergency credential is separately authorized
          # and created; BOOT-013 (deny, don't substitute) is the expected
          # behavior until then.
          emergency_credential_path =
            if inst.emergencyCredentialSecret != null then
              config.sops.secrets.${inst.emergencyCredentialSecret}.path
            else
              null;
          break_glass_flag_path = "${instRoot}/break-glass.flag";
        }
      );
    in
    {
      activation = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/mkdir -p "${instRoot}"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/chmod 700 "${instRoot}"
      '';

      service = {
        Unit = {
          Description = "Hermes host-local policy endpoint (Bootstrap tier) - actor: ${name}";
          After = [ "network-online.target" ];
          Wants = [ "network-online.target" ];
        };
        Service = {
          Type = "simple";
          ExecStart = "${pkgs.python3}/bin/python3 ${endpointSrc}/endpoint.py --config ${configFile} --port ${toString inst.port}";
          WorkingDirectory = instRoot;
          Restart = "on-failure";
          RestartSec = 5;
          # Anti-restart-amplification backstop (BOOT-025) independent of
          # any application-level logic: a unit stuck crash-looping stops
          # being retried and alerts, rather than spinning forever.
          StartLimitIntervalSec = 120;
          StartLimitBurst = 10;
        };
        Install.WantedBy = [ "default.target" ];
      };
    };

  instanceType = lib.types.submodule {
    options = {
      port = lib.mkOption {
        type = lib.types.port;
        description = "Local port this instance listens on (127.0.0.1 only). Not yet referenced by any Hermes profile - wiring Hermes to use it is a separate, explicitly-authorized Phase 2 action.";
      };
      priority = lib.mkOption {
        type = lib.types.enum [
          "P0"
          "P1"
          "P2"
          "P3"
        ];
        description = "Static Bootstrap priority for this actor (00-program-spec.md: unverifiable per-request lineage defaults to autonomous, so Bootstrap uses a fixed per-actor value).";
      };
      continuityClass = lib.mkOption {
        type = lib.types.enum [
          "automatic"
          "automatic-read-only"
          "human-present"
          "manual-break-glass"
          "unavailable"
        ];
        default = "automatic-read-only";
        description = "Orthogonal to priority - whether/how this actor's work may run outside the normal Eros control plane.";
      };
      erosBaseUrl = lib.mkOption {
        type = lib.types.str;
        description = "Stable Eros LiteLLM base URL, e.g. http://eros.tail0e55.ts.net:4000";
      };
      erosTailscaleIp = lib.mkOption {
        type = lib.types.str;
        description = "Eros's pinned Tailscale IP, used ONLY for outage-classifier health probing - never as the forward target for a real request.";
      };
      erosPort = lib.mkOption {
        type = lib.types.port;
        default = 4000;
      };
      erosApiKeySecret = lib.mkOption {
        type = lib.types.str;
        description = "Name of the sops secret holding this actor's dedicated Eros virtual key (see secrets.yaml eros_litellm_key_*).";
      };
      emergencyCredentialSecret = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "Name of the sops secret holding this actor's emergency continuity credential, if one has been provisioned. null (the Bootstrap default) means continuity correctly denies rather than substituting a normal key (BOOT-013).";
      };
      monthlyBudgetUsd = lib.mkOption {
        type = lib.types.float;
        default = 20.0;
        description = "Bootstrap-scale placeholder, matching the test budget on this actor's Eros virtual key. Not a production recommendation - see execution-contract.md 14 for the PO-approval split.";
      };
      expectedBurn1hUsd = lib.mkOption {
        type = lib.types.float;
        default = 0.5;
        description = "Bootstrap-scale placeholder for anomaly-spike detection.";
      };
    };
  };
in
{
  options.profiles.hermesPolicyEndpoint.instances = lib.mkOption {
    type = lib.types.attrsOf instanceType;
    default = { };
    description = ''
      Host-local policy endpoint instances (Bootstrap tier), one per
      Hermes profile/surface. Each is a small stdlib-only Python service
      forwarding to Eros with local, Eros-independent accounting and
      continuity classification - see endpoint.py. Deployed running and
      healthy but NOT referenced by any Hermes profile's model.base_url;
      wiring a profile to actually use one is a separate Phase 2 canary
      action requiring its own explicit approval.
    '';
  };

  config =
    let
      instances = lib.mapAttrs mkInstance cfg.instances;
    in
    lib.mkIf (cfg.instances != { }) {
      home.activation = lib.mapAttrs' (
        name: inst: lib.nameValuePair "hermesPolicyEndpoint_${name}" inst.activation
      ) instances;

      systemd.user.services = lib.mapAttrs' (
        name: inst: lib.nameValuePair "hermes-policy-endpoint-${name}" inst.service
      ) instances;
    };
}
