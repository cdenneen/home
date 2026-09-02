{
  config,
  pkgs,
  lib,
  ...
}:
let
  litellmPort = 4000;
  litellmEnvFile = "/run/eros-litellm/env";
  litellmConfigFile = "/run/eros-litellm/config.yaml";
  omniroutePort = 20128;
  qdrantPort = 6333;
in
{
  networking.hostName = "eros";
  ec2.efi = true;

  # CPU-only LLM gateway: retain the host tooling but do not run a desktop.
  profiles.gui.enable = false;
  services.desktopManager.plasma6.enable = false;
  services.xserver.desktopManager.xfce.enable = false;
  services.xserver.displayManager.lightdm.enable = false;
  services.displayManager.sddm.enable = false;

  services.tailscale = {
    enable = true;
    openFirewall = false;
  };

  # Keep the raw Ollama API local. Clients use the authenticated LiteLLM
  # gateway on the tailnet; do not expose port 11434.
  services.ollama = {
    enable = true;
    host = "127.0.0.1";
    openFirewall = false;
    loadModels = [
      "qwen2.5-coder:7b"
      "qwen3-embedding:0.6b"
    ];
    environmentVariables = {
      OLLAMA_MAX_LOADED_MODELS = "2";
      OLLAMA_CONTEXT_LENGTH = "32768";
      OLLAMA_NUM_PARALLEL = "1";
    };
  };

  virtualisation.oci-containers = {
    backend = "podman";
    containers = {
      qdrant = {
        image = "qdrant/qdrant:v1.18.3@sha256:0bd98fa7977f1e75694779359ca4e212822e5a71334e28421182f72f209d5286";
        ports = [ "127.0.0.1:${toString qdrantPort}:6333" ];
        volumes = [ "/var/lib/qdrant:/qdrant/storage:U" ];
        autoStart = true;
      };
      litellm = {
        image = "ghcr.io/berriai/litellm:v1.94.0@sha256:65d84a2282137b4dc73bbe184650a7c807177c533e4223b3bfbc87963fe3fabe";
        volumes = [ "${litellmConfigFile}:/app/config.yaml:ro" ];
        extraOptions = [
          "--env-file=${litellmEnvFile}"
          "--network=host"
        ];
        cmd = [
          "--config"
          "/app/config.yaml"
          "--host"
          "127.0.0.1"
          "--port"
          (toString litellmPort)
        ];
        autoStart = true;
      };
    };
  };

  systemd.tmpfiles.rules = [ "d /var/lib/qdrant 0750 root root -" ];

  services.postgresql = {
    enable = true;
    ensureDatabases = [ "litellm" ];
    ensureUsers = [
      {
        name = "litellm";
        ensureDBOwnership = true;
      }
    ];
    authentication = lib.mkAfter ''
      host litellm litellm 127.0.0.1/32 scram-sha-256
    '';
    settings.password_encryption = "scram-sha-256";
  };

  sops.secrets = {
    eros_litellm_master_key = {
      owner = "root";
      group = "root";
      mode = "0400";
    };
    eros_litellm_db_password = {
      owner = "root";
      group = "root";
      mode = "0400";
    };
    eros_litellm_salt_key = {
      owner = "root";
      group = "root";
      mode = "0400";
    };
    openai_api_key = {
      owner = "root";
      group = "root";
      mode = "0400";
    };
    gemini_api_key = {
      owner = "root";
      group = "root";
      mode = "0400";
    };
    omniroute_client_key = {
      owner = "root";
      group = "root";
      mode = "0400";
      # NOT a pre-existing external secret - see G-DR-PREP-1 notes below.
      # Bootstrap once via: POST /api/keys {scopes:["self:usage"]} against
      # a running omniroute.service, then sops-encrypt the returned key into
      # this path (requires local age identity - see recovery manifest).
    };
  };

  systemd.services.eros-litellm-env = {
    description = "Render the LiteLLM secret environment";
    before = [ "podman-litellm.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      UMask = "0077";
    };
    path = [ pkgs.coreutils ];
    script = ''
      set -euo pipefail

      read_secret() {
        local secret_file="$1"
        local secret_name="$2"
        if [ ! -r "$secret_file" ]; then
          echo "Missing $secret_name at $secret_file" >&2
          exit 1
        fi
        ${pkgs.coreutils}/bin/tr -d '\n\r' < "$secret_file"
      }

      read_secret_optional() {
        local secret_file="$1"
        local secret_name="$2"
        if [ ! -r "$secret_file" ]; then
          echo "Missing $secret_name at $secret_file - OmniRoute-backed routes will fail auth until this is bootstrapped (see scripts/eros-recovery/); other LiteLLM routes are unaffected." >&2
          echo ""
          return 0
        fi
        ${pkgs.coreutils}/bin/tr -d '\n\r' < "$secret_file"
      }

      ${pkgs.coreutils}/bin/install -d -m 0700 /run/eros-litellm
      ${pkgs.coreutils}/bin/install -m 0600 /dev/null "${litellmEnvFile}"
      {
        printf 'LITELLM_MASTER_KEY=%s\n' "$(read_secret "${config.sops.secrets.eros_litellm_master_key.path}" "LiteLLM master key")"
        printf 'LITELLM_SALT_KEY=%s\n' "$(read_secret "${config.sops.secrets.eros_litellm_salt_key.path}" "LiteLLM salt key")"
        printf 'DATABASE_URL=postgresql://litellm:%s@127.0.0.1:5432/litellm\n' "$(read_secret "${config.sops.secrets.eros_litellm_db_password.path}" "LiteLLM database password")"
        printf 'OPENAI_API_KEY=%s\n' "$(read_secret "${config.sops.secrets.openai_api_key.path}" "OpenAI key")"
        printf 'GEMINI_API_KEY=%s\n' "$(read_secret "${config.sops.secrets.gemini_api_key.path}" "Gemini key")"
        printf 'OMNIROUTE_CLIENT_KEY=%s\n' "$(read_secret_optional "${config.sops.secrets.omniroute_client_key.path}" "OmniRoute client key")"
        printf 'QDRANT_API_BASE=http://127.0.0.1:%s\n' "${toString qdrantPort}"
        printf 'QDRANT_VECTOR_SIZE=1024\n'
      } > "${litellmEnvFile}"
    '';
  };

  systemd.services.eros-litellm-config = {
    description = "Render the LiteLLM configuration";
    before = [ "podman-litellm.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      UMask = "0077";
    };
    path = [ pkgs.coreutils ];
    script = ''
      set -euo pipefail
      ${pkgs.coreutils}/bin/install -d -m 0700 /run/eros-litellm
      cat > "${litellmConfigFile}" <<'EOF'
      model_list:
        # --- Legacy aliases (existing live consumers; kept for compatibility) ---
        - model_name: coding
          litellm_params:
            model: ollama/qwen2.5-coder:7b
            api_base: http://127.0.0.1:11434
          model_info:
            max_input_tokens: 28672
            max_output_tokens: 4096
        - model_name: local-embed
          litellm_params:
            model: ollama/qwen3-embedding:0.6b
            api_base: http://127.0.0.1:11434
        - model_name: coding-openai
          litellm_params:
            model: openai/gpt-5-mini
            api_key: os.environ/OPENAI_API_KEY
        - model_name: openai/*
          litellm_params:
            model: openai/*
            api_key: os.environ/OPENAI_API_KEY
        - model_name: coding-haiku
          litellm_params:
            model: bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0
            aws_region_name: us-east-1
        - model_name: coding-gemini
          litellm_params:
            model: gemini/gemini-2.5-flash
            api_key: os.environ/GEMINI_API_KEY
        # Fixed 2026-08-27: was bedrock/us.anthropic.claude-sonnet-4-6 ($3/$15 per
        # Mtok), which is *more expensive* than Sonnet 5 ($2/$10 per Mtok) despite
        # being the older model. Live traffic was silently overpaying since this
        # route went into use. See phase1-attribution-and-counterfactual.md.
        # cache_control_injection_points added 2026-08-29: this route (bedrock/
        # claude-sonnet-5, called by the eros-nyx-all-routing / eros-VNJTECMBCD-
        # all-routing generic keys - a legacy coding CLI worker, not a Hermes
        # gateway) showed the same sustained-tool-loop shape as the already-proven
        # Nyx EKS tier2-general fix: real traffic on 2026-08-27 02:17-02:23 grew
        # 110,864 -> 133,689 prompt tokens across dozens of turns in ~6 minutes,
        # $0.37-0.45/call at the uncached rate, zero cache_read/cache_creation
        # ever recorded. Validated on a bounded test route before applying here,
        # INCLUDING a tool-calling check this workload needed that tier2-general's
        # validation didn't: a `tools`-bearing request only cached correctly once
        # a `location: tool_config` breakpoint was added alongside system/trailing-
        # message - system+trailing-message alone (tier2-general's exact config)
        # silently produced a 100% cache MISS on every call once `tools` was
        # present, with no error, same cost as no caching at all. Confirmed the
        # 3-point form below handles tools-present AND tools-absent turns in the
        # same conversation correctly (cache write once cache-read 143,757/143,757
        # across 3 real turns of that shape). tier2-general is deliberately left
        # untouched - its 2-point config has proven correct for its own real
        # traffic and is out of scope for alteration this slice.
        # Reusable for any future route with this same tools-capable, repeated-
        # prefix shape: alias *eros_cache_points_with_tools rather than retyping.
        - model_name: coding-strong
          litellm_params:
            model: bedrock/us.anthropic.claude-sonnet-5
            aws_region_name: us-east-1
            cache_control_injection_points: &eros_cache_points_with_tools
              - location: tool_config
                control:
                  type: ephemeral
              - location: message
                role: system
                control:
                  type: ephemeral
              - location: message
                index: -1
                control:
                  type: ephemeral

        - model_name: g2-omniroute-openai-gpt4o-mini
          litellm_params:
            model: openai/gpt-4o-mini
            api_base: http://127.0.0.1:20128/v1
            api_key: os.environ/OMNIROUTE_CLIENT_KEY
        - model_name: g5-omniroute-bedrock-haiku
          litellm_params:
            model: openai/anthropic.claude-3-haiku-20240307-v1:0
            api_base: http://127.0.0.1:20128/v1
            api_key: os.environ/OMNIROUTE_CLIENT_KEY
          model_info:
            input_cost_per_token: 0.00000025
            output_cost_per_token: 0.00000125
        # --- Stable capability-tier routes (00-program-spec.md route contract) ---
        - model_name: tier0-local
          litellm_params:
            model: ollama/qwen2.5-coder:7b
            api_base: http://127.0.0.1:11434
          model_info:
            max_input_tokens: 28672
            max_output_tokens: 4096
        - model_name: tier1-general
          litellm_params:
            model: openai/gemini-2.5-flash
            api_base: http://127.0.0.1:20128/v1
            api_key: os.environ/OMNIROUTE_CLIENT_KEY
            drop_params: true
            additional_drop_params:
              - x_hermes_source
          model_info:
            input_cost_per_token: 0.00000015
            output_cost_per_token: 0.0000006
        - model_name: tier1-coding
          litellm_params:
            model: openai/gpt-5-mini
            api_key: os.environ/OMNIROUTE_CLIENT_KEY
            api_base: http://127.0.0.1:20128/v1
        # Consolidated cheap/fast tier (2026-09-02): additive alongside
        # tier1-general/tier1-coding above, not a replacement yet. Once every
        # consumer requests `mini` instead of tier1-general/tier1-coding
        # directly, those two entries retire - see `mini`'s fallback below,
        # which reuses tier1-coding rather than duplicating it.
        - model_name: mini
          litellm_params:
            model: openai/gemini-2.5-flash
            api_base: http://127.0.0.1:20128/v1
            api_key: os.environ/OMNIROUTE_CLIENT_KEY
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        # cache_control_injection_points added 2026-08-28: validated on a bounded
        # test route (154.8K-token stable prefix, 3-turn A/B) before applying here -
        # cache write $0.4257/3655ms turn 1, cache read $0.0341/1716ms turn 2,
        # $0.0342/1699ms turn 3 (92% cost cut, 53% latency cut, sustained across
        # consecutive turns). Model/capability unchanged - this only changes how
        # Bedrock bills/serves an already-identical prefix. tier2-coding/
        # tier2-research/tier3-quality intentionally NOT touched yet - bounded to
        # the one route with proven real organic long-tool-loop usage (Nyx EKS).
        # ARCHITECTURE EXCEPTION (2026-08-31, G9/G10 investigation): this route
        # intentionally stays on direct LiteLLM->Bedrock rather than OmniRoute.
        # Root cause (confirmed via source inspection + live debug-log capture):
        # LiteLLM cache_control_injection_points is implemented ONLY in
        # llms/bedrock/chat/converse_transformation.py, gated to
        # custom_llm_provider="bedrock". Routing this model through OmniRoute
        # (custom_llm_provider="openai" + api_base=OmniRoute) means cache_control
        # is never attached to the request at all - confirmed empirically: a
        # repeated 6989-token prefix test via OmniRoute showed turn 2 SLOWER than
        # turn 1 (no cache read), vs this direct route real cache_creation/
        # cache_read tokens and a documented 92%% cost cut. Classified
        # CACHE_NOT_REQUESTED, not an OmniRoute defect - OmniRoute never receives
        # cache metadata to translate or drop.
        # This route remains governed by Eros and accounted by LiteLLM - NOT an
        # unmanaged bypass. Revisit only when: upstream LiteLLM supports cache
        # injection for the openai-compatible path; OmniRoute gains a native
        # equivalent; or another candidate/path proves cheaper per verified
        # outcome. See phase1-cache-root-cause.md (recovery/portability manifest).
        - model_name: tier2-general
          litellm_params:
            model: bedrock/us.anthropic.claude-sonnet-5
            aws_region_name: us-east-1
            drop_params: true
            additional_drop_params:
              - x_hermes_source
            cache_control_injection_points:
              - location: message
                role: system
                control:
                  type: ephemeral
              - location: message
                index: -1
                control:
                  type: ephemeral
        - model_name: tier2-coding
          litellm_params:
            model: openai/us.anthropic.claude-sonnet-5
            api_base: http://127.0.0.1:20128/v1
            api_key: os.environ/OMNIROUTE_CLIENT_KEY
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        # Consolidated quality-coding tier (2026-09-02): additive alongside
        # tier2-coding/tier2-research above. Same-tier redundancy across
        # paths, NOT the "generic 429/error fallback" this file's
        # router_settings comment below still correctly bans - see that
        # comment for the incident (coding-strong -> coding-gemini silently
        # collapsing Claude-tier onto Gemini Flash) this must never repeat.
        # Every candidate in `coding-strong`'s own fallback chain is the same
        # Sonnet-5/GPT-5.4 quality class; nothing here ever drops to mini.
        #
        # Motivating evidence (2026-08-25..09-01, OmniRoute call_logs): Sonnet
        # traffic through OmniRoute (tier2-coding/tier2-research) ran ~57%
        # success over 7 days; 74%% of the failures were OmniRoute's own
        # local request-queue timeout (resilienceSettings.requestQueue.
        # maxWaitMs), not a Bedrock/upstream problem. This chain gives that
        # traffic somewhere real to go instead of failing outright.
        - model_name: coding-strong
          litellm_params:
            model: openai/bedrock/us.anthropic.claude-sonnet-5
            # co-located on eros today; if omniroute ever moves to its own
            # host, this becomes http://eros.tail0e55.ts.net:20128/v1
            api_base: http://127.0.0.1:20128/v1
            api_key: os.environ/OMNIROUTE_CLIENT_KEY
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        - model_name: tier2-research
          litellm_params:
            model: openai/us.anthropic.claude-sonnet-5
            api_base: http://127.0.0.1:20128/v1
            api_key: os.environ/OMNIROUTE_CLIENT_KEY
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        - model_name: tier3-quality
          litellm_params:
            model: openai/global.anthropic.claude-opus-5
            api_base: http://127.0.0.1:20128/v1
            api_key: os.environ/OMNIROUTE_CLIENT_KEY
        # `quality` (2026-09-02): additive alias for tier3-quality's exact
        # model. No fallback, same as tier3-quality itself - Opus is its own
        # capability class; failure must reject, never silently become
        # coding-strong's Sonnet-5.
        - model_name: quality
          litellm_params:
            model: openai/global.anthropic.claude-opus-5
            api_base: http://127.0.0.1:20128/v1
            api_key: os.environ/OMNIROUTE_CLIENT_KEY
        # tier4-frontier deliberately has no fallback and is not part of any
        # fallback chain below - explicit-only, separate key at the governor
        # layer (00-program-spec.md: "explicit only; no automatic fallback").
        - model_name: gpt-5.4
          litellm_params:
            model: openai/gpt-5.4
            api_base: http://127.0.0.1:20128/v1
            api_key: os.environ/OMNIROUTE_CLIENT_KEY
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        - model_name: gpt-5.6-terra
          litellm_params:
            model: openai/gpt-5.6-terra
            api_base: http://127.0.0.1:20128/v1
            api_key: os.environ/OMNIROUTE_CLIENT_KEY
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        - model_name: axis-claude-sonnet-4-6
          litellm_params:
            model: bedrock/us.anthropic.claude-sonnet-4-6
            aws_region_name: us-east-1
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        - model_name: tier4-frontier
          litellm_params:
            model: openai/gpt-5.6-sol
            api_key: os.environ/OPENAI_API_KEY
      general_settings:
        master_key: os.environ/LITELLM_MASTER_KEY
        database_url: os.environ/DATABASE_URL
      litellm_settings:
        # Bootstrap default is cache bypass everywhere (01-eros-inference-fabric.md).
        # Previously cache:true + router_settings.cache_responses:false were both
        # present at once (ambiguous, flagged in 07-cache-and-retrieval.md) - fixed
        # by disabling caching outright until 07's adoption sequence is run.
        cache: false
        drop_params: true
        additional_drop_params:
          - x_hermes_source
      router_settings:
        cache_responses: false
        # Cross-tier/generic fallback is still banned - 01-eros-inference-
        # fabric.md's rule stands, and the incident that produced it is real:
        # an earlier `coding-strong: [coding-gemini]` entry once silently
        # collapsed a Claude-tier request onto Gemini Flash on failure -
        # undetectable capability downgrade. That must never happen again.
        #
        # What's below is narrower and different in kind: same-tier
        # redundancy across paths for one named model, not a downgrade path.
        # `coding-strong` only ever falls back to gpt-5.4 (same quality
        # class, different provider) or tier2-general (the literal same
        # Sonnet-5, direct-Bedrock path instead of via OmniRoute) - see the
        # comment on `coding-strong` above for the OmniRoute-reliability
        # evidence motivating this. `mini` only falls back to tier1-coding,
        # itself a cheap/fast-tier model, not a downgrade from mini's own
        # tier. tier4-frontier/tier2-general/quality getting no entry at all
        # would already mean no fallback (litellm only fires a fallback for
        # a model that has one) - the explicit empty lists below are just
        # that intent written down, so a future generic/wildcard entry can't
        # silently start catching them without someone having to touch these
        # lines first.
        fallbacks:
          - coding-strong: [gpt-5.4, tier2-general]
          - mini: [tier1-coding]
          - tier4-frontier: []
          - tier2-general: []
          - quality: []
        num_retries: 1
        timeout: 90
      EOF
      ${pkgs.coreutils}/bin/chmod 0600 "${litellmConfigFile}"
    '';
  };

  systemd.services.eros-litellm-db-user = {
    description = "Set the LiteLLM PostgreSQL role password";
    after = [ "postgresql.service" ];
    requires = [ "postgresql.service" ];
    before = [ "podman-litellm.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      UMask = "0077";
    };
    path = [
      config.services.postgresql.package
      pkgs.coreutils
      pkgs.util-linux
    ];
    script = ''
      set -euo pipefail

      password_file="${config.sops.secrets.eros_litellm_db_password.path}"
      if [ ! -r "$password_file" ]; then
        echo "Missing LiteLLM database password at $password_file" >&2
        exit 1
      fi

      password="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "$password_file")"
      if [ -z "$password" ]; then
        echo "LiteLLM database password is empty" >&2
        exit 1
      fi
      case "$password" in
        *[!0-9a-f]*)
          echo "LiteLLM database password must be lowercase hexadecimal" >&2
          exit 1
          ;;
      esac

      printf "ALTER ROLE litellm PASSWORD '%s';\\n" "$password" \
        | ${pkgs.util-linux}/bin/runuser -u postgres -- ${config.services.postgresql.package}/bin/psql --dbname=postgres --set=ON_ERROR_STOP=1
    '';
  };

  systemd.services.podman-litellm = {
    requires = [
      "eros-litellm-db-user.service"
      "eros-litellm-env.service"
      "eros-litellm-config.service"
      "ollama.service"
      "podman-qdrant.service"
    ];
    after = [
      "eros-litellm-db-user.service"
      "eros-litellm-env.service"
      "eros-litellm-config.service"
      "ollama.service"
      "podman-qdrant.service"
    ];
  };

  systemd.services.omniroute = {
    description = "OmniRoute local AI gateway";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    wantedBy = [ "multi-user.target" ];
    path = [ pkgs.nodejs_24 ];
    environment = {
      HOME = config.users.users.cdenneen.home;
      HOSTNAME = "127.0.0.1";
      PORT = toString omniroutePort;
      DATA_DIR = "${config.users.users.cdenneen.home}/.omniroute";
    };
    serviceConfig = {
      Type = "simple";
      User = "cdenneen";
      Group = "users";
      WorkingDirectory = config.users.users.cdenneen.home;
      ExecStart = "${config.users.users.cdenneen.home}/.local/bin/omniroute --no-open";
      Restart = "on-failure";
      RestartSec = "5s";
    };
  };

  systemd.services.tailscale-serve-eros = {
    description = "Expose LiteLLM, OmniRoute, and Qdrant over Tailscale";
    after = [
      "tailscaled.service"
      "podman-litellm.service"
      "omniroute.service"
      "podman-qdrant.service"
    ];
    requires = [
      "tailscaled.service"
      "podman-litellm.service"
      "omniroute.service"
      "podman-qdrant.service"
    ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig.Type = "oneshot";
    path = [ pkgs.tailscale ];
    script = ''
      set -euo pipefail
      if ! ${pkgs.tailscale}/bin/tailscale status >/dev/null 2>&1; then
        echo "Tailscale is not authenticated; skipping LiteLLM serve"
        exit 0
      fi
      ${pkgs.tailscale}/bin/tailscale serve --bg --yes --tcp ${toString litellmPort} 127.0.0.1:${toString litellmPort}
      ${pkgs.tailscale}/bin/tailscale serve --bg --yes --tcp ${toString omniroutePort} 127.0.0.1:${toString omniroutePort}
      # Shared AI Services MVP: policy-endpoint instances on Ghost/Nyx need
      # to reach Qdrant for shared-reuse retrieval/promotion
      # (shared_intelligence.py) - previously loopback-only, undiscovered
      # until the first real cross-host retrieval attempt hung on
      # POST/PUT (GET happened to work locally-only in prior testing; this
      # is the first time it's been reached from another host at all).
      ${pkgs.tailscale}/bin/tailscale serve --bg --yes --tcp ${toString qdrantPort} 127.0.0.1:${toString qdrantPort}
    '';
  };

  services.udisks2.enable = lib.mkForce false;
  services.openssh.settings.PermitRootLogin = lib.mkForce "prohibit-password";
  users.users.cdenneen.openssh.authorizedKeys.keys = [
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAII1avpzyzr4rhp/LyD9JrcO+DJP+6pBMwbOglSBXHudF cdenneen_ed25519_2024"
  ];

  services.amazon-cloudwatch-agent = {
    enable = true;
    mode = "ec2";
    user = "root";
    commonConfiguration = {
      credentials = {
        imds_version = 2;
      };
    };
    configuration = {
      agent = {
        metrics_collection_interval = 60;
        region = "us-east-1";
        logfile = "/var/log/amazon-cloudwatch-agent/amazon-cloudwatch-agent.log";
      };
      metrics = {
        namespace = "CWAgent";
        append_dimensions = {
          ImageId = "\${aws:ImageId}";
          InstanceId = "\${aws:InstanceId}";
          InstanceType = "\${aws:InstanceType}";
          AutoScalingGroupName = "\${aws:AutoScalingGroupName}";
        };
        aggregation_dimensions = [ [ "InstanceId" ] ];
        metrics_collected = {
          cpu = {
            measurement = [
              "cpu_usage_idle"
              "cpu_usage_iowait"
              "cpu_usage_user"
              "cpu_usage_system"
            ];
            totalcpu = true;
            metrics_collection_interval = 60;
          };
          mem = {
            measurement = [
              "mem_used_percent"
              "mem_available"
              "mem_available_percent"
            ];
            metrics_collection_interval = 60;
          };
          disk = {
            measurement = [ "used_percent" ];
            resources = [ "/" ];
            drop_device = true;
            metrics_collection_interval = 60;
          };
          diskio = {
            measurement = [
              "reads"
              "writes"
              "read_bytes"
              "write_bytes"
              "io_time"
            ];
            resources = [ "*" ];
            metrics_collection_interval = 60;
          };
          net = {
            measurement = [
              "bytes_sent"
              "bytes_recv"
            ];
            resources = [ "*" ];
            metrics_collection_interval = 60;
          };
          swap = {
            measurement = [ "used_percent" ];
            metrics_collection_interval = 60;
          };
          processes = {
            measurement = [
              "running"
              "sleeping"
              "zombies"
              "total"
            ];
            metrics_collection_interval = 60;
          };
        };
      };
    };
  };

  services.amazon-ssm-agent.enable = true;

  # Matches running system (do not change after initial install)
  # Match global default; do not downgrade
  system.stateVersion = lib.mkForce "26.05";

  # Filesystems.
  # NOTE: Some upstream EC2/EFI modules also declare an ESP at /boot.
  # We force the full fileSystems attrset here so the ESP is only mounted
  # at /boot/efi; /boot must remain on the root filesystem for NixOS kernels.
  fileSystems = lib.mkForce {
    "/" = {
      device = "/dev/disk/by-uuid/f222513b-ded1-49fa-b591-20ce86a2fe7f";
      fsType = "ext4";
    };

    "/boot/efi" = {
      device = "/dev/disk/by-uuid/12CE-A600";
      fsType = "vfat";
    };
  };

  # Leave /boot on the root filesystem; only mount the ESP at /boot/efi.
  # This avoids running out of space on the ESP when storing kernels/initrd.

  # UEFI + GRUB (current system uses GRUB on EFI)
  boot.loader = {
    efi = {
      # EC2 UEFI typically does not provide persistent EFI variables.
      canTouchEfiVariables = false;
      efiSysMountPoint = "/boot/efi";
    };
    grub = {
      splashImage = lib.mkForce null;
      enable = true;
      configurationLimit = 3;
      efiSupport = true;
      # Install via the UEFI removable-media fallback path (EFI/BOOT).
      efiInstallAsRemovable = true;
      device = "nodev";
    };
  };

  # On EC2 we install GRUB as "removable" (EFI/BOOT/BOOTAA64.EFI). In that mode
  # GRUB tends to use the ESP for configuration, which is too small for storing
  # NixOS kernels/initrds across generations.
  #
  # We generate a small ESP grub.cfg that:
  # - First entry chainloads the real GRUB menu from the root filesystem.
  # - Second entry boots the currently selected system profile directly.
  #
  # Important: avoid referencing config.system.build.* here; it can create module
  # evaluation recursion. Use stable on-disk paths instead.
  boot.loader.grub.extraInstallCommands = ''
        ${pkgs.coreutils}/bin/mkdir -p "${config.boot.loader.efi.efiSysMountPoint}/grub"
        ${pkgs.coreutils}/bin/cat > "${config.boot.loader.efi.efiSysMountPoint}/grub/grub.cfg" <<'EOF'
        # Autogenerated (NixOS): ESP GRUB config for EC2.
        set timeout=1
        set timeout_style=menu
        set default=0

        function chainload_rootfs_menu {
          insmod part_gpt
          insmod ext2
          insmod search_fs_file
          if search --no-floppy --file /boot/grub/grub.cfg --set=root; then
            set prefix=($root)/boot/grub
            configfile ($root)/boot/grub/grub.cfg
          fi
        }

        function boot_current_profile {
          insmod part_gpt
          insmod ext2
          insmod search_fs_file
          insmod linux
          if search --no-floppy --file /nix/var/nix/profiles/system/init --set=root; then
            linux ($root)/nix/var/nix/profiles/system/kernel init=/nix/var/nix/profiles/system/init console=ttyS0,115200n8
            initrd ($root)/nix/var/nix/profiles/system/initrd
            boot
          fi
        }

        menuentry "NixOS (full menu)" --class nixos --unrestricted {
          chainload_rootfs_menu
          echo "GRUB: failed to chainload /boot/grub/grub.cfg"
          sleep 5
        }

        menuentry "NixOS (current system profile)" --class nixos --unrestricted {
          boot_current_profile
          echo "GRUB: failed to boot /nix/var/nix/profiles/system"
          sleep 5
        }
    EOF
  '';

  # Networking (DHCP on ens5)
  networking.useDHCP = false;
  networking.interfaces.ens5.useDHCP = true;


  # Drift guard (G-DR-PREP-1): detects when the running eros-litellm
  # config.yaml has diverged from what the CURRENTLY DEPLOYED flake pin
  # (/etc/nixos's flake.lock) would generate. Reuses the exact oneshot
  # config-render logic already in this file rather than a new
  # controller/service - this is a read-only comparison, run on demand
  # or from a monitoring check, not a background daemon.
  environment.systemPackages = [
    (pkgs.writeShellScriptBin "eros-drift-check" ''
      set -euo pipefail
      echo "Evaluating declarative config from the deployed flake pin..." >&2
      generated="$(${pkgs.nix}/bin/nix eval --raw path:/etc/nixos#nixosConfigurations.eros.config.systemd.services.eros-litellm-config.script \
        | ${pkgs.gnused}/bin/sed -n '/^model_list:/,/^EOF$/p' | ${pkgs.gnused}/bin/sed '$d')"
      live="$(${pkgs.coreutils}/bin/cat /run/eros-litellm/config.yaml)"
      if [ "$generated" = "$live" ]; then
        echo "OK: /run/eros-litellm/config.yaml matches the deployed declarative source."
        exit 0
      fi
      echo "DRIFT DETECTED: /run/eros-litellm/config.yaml differs from what the deployed" >&2
      echo "flake pin would generate. A reboot/rebuild would silently discard the live" >&2
      echo "difference shown below. Persist it to hosts/nixos/eros.nix before relying on it." >&2
      ${pkgs.diffutils}/bin/diff <(echo "$generated") <(echo "$live") || true
      exit 1
    '')
  ];
  profiles.defaults.enable = true;
}
