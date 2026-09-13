{
  config,
  lib,
  pkgs,
  ...
}:
let
  roleNames = [
    "chief-of-staff"
    "researcher"
    "architect"
    "coder"
    "tester"
    "reviewer"
    "ops"
  ];
  peerUrls = {
    ghost.url = "http://100.114.242.29:8642";
    nyx.url = "http://100.80.58.4:8642";
  };
  mkSecretsCommand =
    slackEnvPath:
    pkgs.writeShellScript "hermes-mesh-secrets" ''
      set -euo pipefail

      emit_secret() {
        name="$1"
        path="$2"
        value="$(${pkgs.coreutils}/bin/tr -d '\r\n' < "$path")"
        [ -n "$value" ]
        ${pkgs.coreutils}/bin/printf '%s=%s\n' "$name" "$value"
      }

      emit_secret EROS_HERMES_AGENTS_KEY ${lib.escapeShellArg config.sops.secrets.eros_litellm_key_hermes_agents.path}
      emit_secret API_SERVER_KEY ${lib.escapeShellArg config.sops.secrets.hermes_mesh_api_key_ghost.path}
      emit_secret HERMES_PEER_GHOST_KEY ${lib.escapeShellArg config.sops.secrets.hermes_mesh_api_key_ghost.path}
      emit_secret HERMES_PEER_NYX_KEY ${lib.escapeShellArg config.sops.secrets.hermes_mesh_api_key_nyx.path}
      ${lib.optionalString (slackEnvPath != null) ''
        ${pkgs.coreutils}/bin/cat ${lib.escapeShellArg slackEnvPath}
      ''}
    '';
  baseSecretsCommand = mkSecretsCommand null;
  chiefSecretsCommand = mkSecretsCommand config.sops.secrets.hermes_slack_env_ghost_chief.path;
  erosMcp = name: {
    url = "http://eros.tail0e55.ts.net:4000/mcp/${name}";
    headers.Authorization = "Bearer $" + "{EROS_HERMES_AGENTS_KEY}";
    connect_timeout = 30;
    timeout = 180;
  };
  gitlabSaasMcp = pkgs.writeShellScript "hermes-gitlab-saas-mcp" ''
    set -euo pipefail
    token="$(${pkgs.glab}/bin/glab auth token -h gitlab.com)"
    [ -n "$token" ]
    export GITLAB_API_URL="https://gitlab.com/api/v4"
    export GITLAB_PERSONAL_ACCESS_TOKEN="$token"
    export GITLAB_READ_ONLY_MODE="false"
    exec ${pkgs.nodejs_24}/bin/npx -y @zereight/mcp-gitlab
  '';
  mkMeshProfile = model: secretsCommand: extraOverrides: {
    createIfMissing = true;
    modelOverrides = {
      "model.default" = model;
      "model.provider" = "custom";
      "model.base_url" = "http://eros.tail0e55.ts.net:4000/v1";
      "model.api_key" = "$" + "{EROS_HERMES_AGENTS_KEY}";
      "model.api_mode" = "chat_completions";
      "secrets.command.enabled" = true;
      "secrets.command.command" = toString secretsCommand;
      "auxiliary.compression.model" = "nova-2-lite";
      "auxiliary.compression.provider" = "main";
      "auxiliary.title_generation.model" = "nova-2-lite";
      "auxiliary.title_generation.provider" = "main";
      bot_peers = peerUrls;
    }
    // extraOverrides;
  };
  mkNamedMeshProfile =
    name: model: secretsCommand: extraOverrides:
    (mkMeshProfile model secretsCommand (
      {
        "platforms.api_server.enabled" = false;
      }
      // extraOverrides
    ))
    // {
      configHomeRelativePath = ".hermes/profiles/${name}/config.yaml";
    };
  gatewayProfile =
    (mkMeshProfile "claude-haiku-4-5" baseSecretsCommand {
      "gateway.multiplex_profiles" = true;
      "gateway.multiplex_profile_allowlist" = roleNames;
      "gateway.max_concurrent_sessions" = 4;
      "platforms.api_server.enabled" = true;
      "platforms.api_server.extra.host" = "100.114.242.29";
      "platforms.api_server.extra.port" = 8642;
      "platforms.api_server.extra.model_name" = "ghost-mesh";
      "plugins.enabled" = [ "platforms/slack" ];
    })
    // {
      configHomeRelativePath = ".hermes/config.yaml";
    };
  commonSoul = ''
    ## Mesh contract

    Your canonical identity is the profile role plus host, for example `coder@ghost`.
    Record material progress and blockers on the shared Hermes Kanban so Chief of Staff can maintain operational awareness. Send a short status handoff to Chief of Staff when delegated work starts, blocks, enters review, or completes. Never merge, deploy, change infrastructure, or mutate an authoritative external backlog without explicit approval from Chris or delegated approval from Chief of Staff. Preserve the Ghost personal / Nyx work trust boundary.
  '';
in
{
  profiles.hermesAxisControlGateway.enable = false;
  profiles.hermesGateway.enable = false;
  profiles.hermesGatewaySecondary.enable = false;
  profiles.hermesSupervisor.enable = false;
  profiles.hermesWatchdog.enable = false;
  profiles.gitlabMcpProxy.enable = false;
  services.axis-control-observer.enable = false;

  profiles.hermesMesh = {
    enable = true;
    workingDirectory = "/home/cdenneen/src/workspace";
    souls = {
      chief-of-staff = commonSoul + ''
        # Role: Chief of Staff

        You are the unified control plane for personal and work engineering. You may see all project metadata, but corporate code and credentials remain on Nyx. External GitHub or GitLab backlogs are authoritative when a project has one; the Native Hermes Kanban is authoritative for projects without one, initially the personal Nix flake. Reconcile hourly, escalate an unchanged in-progress item after four hours, and mark it operationally stuck after six hours.

        Route refined work with `hermes -p chief-of-staff peer dm ghost/<role>` or `hermes -p chief-of-staff peer dm nyx/<role>`. Use Ghost roles for personal work and Nyx roles for work. Require Architect refinement when acceptance criteria are unclear. Record Nyx status handoffs on the central Ghost Kanban because Nyx profiles cannot write that database directly. Keep an auditable task trail and never grant merge or deployment authority implicitly.
      '';
      researcher = commonSoul + ''
        # Role: Researcher

        Perform evidence-first, read-only research. Separate verified facts from inference, cite sources or command evidence, and return concise findings plus unresolved questions. Do not make repository, backlog, service, or infrastructure changes.
      '';
      architect = commonSoul + ''
        # Role: Architect

        Convert approved outcomes into governance-ready designs, acceptance criteria, task boundaries, rollback plans, and validation evidence. Do not implement unless Chief of Staff explicitly delegates implementation.
      '';
      coder = commonSoul + ''
        # Role: Personal Coder

        Implement only personal work under `/home/cdenneen/src/workspace`. Use feature branches, focused tests, and reviewable commits. Open a PR or MR and hand it to Tester and Reviewer; never merge your own work without approval.
      '';
      tester = commonSoul + ''
        # Role: Personal Tester

        Independently validate personal changes with the narrowest useful tests first, then broader checks. Report exact commands, results, regressions, and residual risk. Do not merge or deploy.
      '';
      reviewer = commonSoul + ''
        # Role: Personal Review Manager

        Review correctness, security, governance compliance, test evidence, and rollback readiness. Approval is explicit and scoped. Never merge unless Chris or Chief of Staff has granted approval authority for that item.
      '';
      ops = commonSoul + ''
        # Role: Personal Ops

        Operate personal services on Ghost only. Prefer read-only diagnosis and declarative changes. Execute deployments, restarts with impact, secret rotation, or destructive cleanup only after explicit approval, and always capture rollback evidence.
      '';
    };
  };

  home.activation.retireLegacyHermes = lib.hm.dag.entryBefore [ "writeBoundary" ] ''
    if [ -z "''${DRY_RUN_CMD:-}" ]; then
      ${pkgs.systemd}/bin/systemctl --user disable --now \
        alpha0-gitlab-nyx-relay.service \
        axis-control-observe.service \
        axis-control-observe.timer \
        axis-control-watchdog.service \
        axis-control-watchdog.timer \
        axis-development-watchdog-backup.service \
        axis-development-watchdog-backup.timer \
        axis-development-watchdog-monitor.service \
        hermes-alpha0-gateway.service \
        hermes-axis-control-scheduler-watchdog.service \
        hermes-axis-control-scheduler-watchdog.timer \
        hermes-axis-control-gateway.service \
        hermes-gateway-secondary.service \
        hermes-gateway.service \
        hermes-policy-endpoint-ghost-alpha0.service \
        hermes-policy-endpoint-ghost-axis-control.service \
        hermes-policy-endpoint-ghost-default.service \
        hermes-stuck-cron-watchdog.service \
        hermes-stuck-cron-watchdog.timer \
        hermes-supervisor-cron.service \
        hermes-watchdog-cron.service \
        hermes-watchdog-cutover.service \
        2>/dev/null || true

      archive_root="$HOME/.hermes-retired/pre-mesh-20260912"
      archive_path="$archive_root/hermes-home"
      retirement_marker="$archive_root/.retired"
      ${pkgs.coreutils}/bin/install -d -m 700 "$archive_root"
      if [ ! -e "$retirement_marker" ]; then
        if [ -e "$archive_path" ]; then
          echo "Refusing unverified pre-existing Hermes archive: $archive_path" >&2
          exit 1
        fi
        if [ -e "$HOME/.hermes" ]; then
          ${pkgs.coreutils}/bin/mv -T "$HOME/.hermes" "$archive_path"
        fi
        ${pkgs.coreutils}/bin/touch "$retirement_marker"
      elif [ -e "$HOME/.hermes/profiles/alpha0" ] \
        || [ -e "$HOME/.hermes/profiles/axis-control" ]; then
        echo "Legacy Hermes state reappeared after retirement; refusing activation" >&2
        exit 1
      fi
    fi
  '';

  profiles.hermesProfileModel.profiles = {
    gateway-router = gatewayProfile;
    chief-of-staff = mkNamedMeshProfile "chief-of-staff" "claude-sonnet-5" chiefSecretsCommand {
      "platforms.slack.enabled" = true;
      mcp_servers = {
        recallium = erosMcp "recallium";
        duckduckgo = erosMcp "duckduckgo";
        context7 = erosMcp "context7";
        gitlab_saas = {
          command = toString gitlabSaasMcp;
          connect_timeout = 30;
          timeout = 180;
        };
      };
    };
    researcher = mkNamedMeshProfile "researcher" "kimi-k2.5" baseSecretsCommand {
      mcp_servers = {
        recallium = erosMcp "recallium";
        duckduckgo = erosMcp "duckduckgo";
      };
    };
    architect = mkNamedMeshProfile "architect" "claude-opus-5" baseSecretsCommand {
      mcp_servers = {
        recallium = erosMcp "recallium";
        context7 = erosMcp "context7";
      };
    };
    coder = mkNamedMeshProfile "coder" "qwen3-coder-next" baseSecretsCommand {
      "mcp_servers.context7" = erosMcp "context7";
    };
    tester = mkNamedMeshProfile "tester" "deepseek-v3.2" baseSecretsCommand {
      "mcp_servers.playwright" = erosMcp "playwright";
    };
    reviewer = mkNamedMeshProfile "reviewer" "claude-sonnet-5" baseSecretsCommand { };
    ops = mkNamedMeshProfile "ops" "claude-sonnet-4-6" baseSecretsCommand { };
  };
}
