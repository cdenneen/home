{
  config,
  lib,
  pkgs,
  ...
}:
let
  roleNames = [
    "chief-of-staff"
    "assistant"
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

      emit_secret EROS_HERMES_AGENTS_KEY ${lib.escapeShellArg config.sops.secrets.eros_litellm_api_key.path}
      emit_secret API_SERVER_KEY ${lib.escapeShellArg config.sops.secrets.hermes_mesh_api_key_ghost.path}
      emit_secret HERMES_PEER_GHOST_KEY ${lib.escapeShellArg config.sops.secrets.hermes_mesh_api_key_ghost.path}
      emit_secret HERMES_PEER_NYX_KEY ${lib.escapeShellArg config.sops.secrets.hermes_mesh_api_key_nyx.path}
      ${lib.optionalString (slackEnvPath != null) ''
        ${pkgs.coreutils}/bin/cat ${lib.escapeShellArg slackEnvPath}
      ''}
    '';
  baseSecretsCommand = mkSecretsCommand null;
  chiefSecretsCommand = mkSecretsCommand config.sops.secrets.hermes_slack_env_ghost_chief.path;
  erosMcp = {
    # Two virtual discovery/call tools front the full permitted catalog.
    url = "http://eros.tail0e55.ts.net:4000/mcp/";
    headers = {
      Authorization = "Bearer $" + "{EROS_HERMES_AGENTS_KEY}";
      "x-eros-consumer" = "ghost";
      "x-eros-trust-domain" = "personal";
      "x-eros-workload" = "hermes";
    };
    connect_timeout = 30;
    timeout = 180;
  };
  gitlabComMcp = pkgs.writeShellScript "hermes-gitlab-com-mcp" ''
    set -euo pipefail
    config="''${GLAB_CONFIG_FILE:-$HOME/.config/glab-cli/config.yml}"
    token="$(${pkgs.gawk}/bin/awk '
      /^hosts:/ { in_hosts=1; next }
      in_hosts && /^[^[:space:]]/ { in_hosts=0 }
      in_hosts && /^    [^[:space:]].*:$/ {
        host=$1; sub(/:$/,"",host); in_target=(host=="gitlab.com"); next
      }
      in_hosts && in_target && /^        token:/ {
        sub(/^        token: */,""); print; exit
      }
    ' "$config")"
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
      mcp_servers = {
        context = erosMcp;
      };
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
      "kanban.default_assignee" = "";
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
    consumer = "ghost-hermes";
    trustDomain = "personal";
    workingDirectory = "/home/cdenneen/src/workspace";
    souls = {
      assistant = commonSoul + ''
        # Role: Personal Assistant

        Handle personal Gmail and Google Calendar on Ghost using only the read-only `personal-google-assistant` skill. Summarize, search, prepare briefs, identify conflicts, and surface action items. Never send or modify mail, labels, or calendar events. Never disclose personal message or calendar content to Nyx; share only the minimum task metadata Chief of Staff needs for coordination.
      '';
      chief-of-staff = commonSoul + ''
        # Role: Chief of Staff

        You are the unified control plane for personal and work engineering. You may see all project metadata, but corporate code and credentials remain on Nyx. External GitHub or GitLab backlogs are authoritative when a project has one; the Native Hermes Kanban is authoritative for projects without one, initially the personal Nix flake. Reconcile hourly, escalate an unchanged in-progress item after four hours, and mark it operationally stuck after six hours.

        After reconciliation, maintain an ordered shortlist of up to five next actionable items per project. Include only open, ready, refined, unassigned, unblocked issues; exclude active, review, scheduled, blocked, control, epic, milestone, and other planning-only items. Rank an approved incident or change window due within seven days first, then authoritative priority, earliest due date, dependency-unblocking value, oldest ready age, and stable external identity. Fewer than five is valid: never invent work to fill the shortlist and never dispatch all five automatically. Shortlisting does not grant merge, deployment, infrastructure-apply, destructive, or production-change authority.

        Route refined work only with `hermes-peer-dispatch start <host>/<role> --idempotency-key <stable-task-key> --board <board> --task <task-id> '<instructions>'`. Each task gets an isolated peer session, so a running task never blocks a correction or unrelated dispatch. Reuse the same idempotency key after an uncertain response; never create a replacement run blindly. Use `hermes-peer-dispatch steer <run-id> '<correction>'` for in-flight corrections and `hermes-peer-dispatch stop <run-id>` for wrong, unsafe, or obsolete work. Do not use `hermes peer dm` or `hermes peer run` for delegated work.

        The deterministic watcher records starts, approval requests, and terminal results on the originating central Kanban task exactly once. Treat `completed` as transport state only: inspect the persisted result and evidence before changing task state. Do not auto-approve a waiting run. Keep at most one active run per role unless explicit parallelism is justified. Use Ghost roles for personal work and Nyx roles for work. Require Architect refinement when acceptance criteria are unclear. Keep an auditable task trail and never grant merge or deployment authority implicitly.
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

  profiles.hermesAssistant = {
    personal.enable = true;
    automation = {
      enable = true;
      slackEnvFile = config.sops.secrets.hermes_slack_env_ghost_chief.path;
      slackChannel = "C0BHLUXQ4EB";
      briefCalendar = "*-*-* 07:30:00 America/New_York";
    };
  };

  profiles.hermesKanbanSync = {
    enable = true;
    outboundEnabled = true;
    settings = {
      logical_projects = [
        {
          slug = "personal-axis";
          name = "Personal AXIS";
          board = "personal-axis";
          description = "GitLab.com Free backlog projection; labels provide epic, roadmap, milestone, and workflow semantics.";
          folders = [
            "/home/cdenneen/src/workspace/personal/work/axis"
            "/home/cdenneen/src/workspace/personal/work/axis-governance"
            "/home/cdenneen/src/workspace/personal/work/axis-lab"
          ];
          primary = "/home/cdenneen/src/workspace/personal/work/axis";
        }
        {
          slug = "personal-nix";
          name = "Personal Nix Flake";
          board = "personal-nix";
          description = "Native Hermes-authoritative backlog for github.com/cdenneen/home.";
          folders = [ "/home/cdenneen/src/workspace/nix/home" ];
          primary = "/home/cdenneen/src/workspace/nix/home";
        }
        {
          slug = "work-eks-platform";
          name = "Work EKS Platform";
          board = "work-eks-platform";
          description = "git.ap.org Premium backlog projection; native group boards, epics, and milestones are authoritative.";
          folders = [ ];
        }
        {
          slug = "work-gitlab";
          name = "Work GitLab";
          board = "work-gitlab";
          description = "git.ap.org Premium backlog projection; native group boards, epics, and milestones are authoritative.";
          folders = [ ];
        }
      ];
      sources = [
        {
          id = "gitlab-com-axis";
          host = "gitlab.com";
          planning_mode = "labels";
          workflow_scheme = "personal-labels";
          transport = "local";
          projects =
            map
              (path: {
                inherit path;
                logical_project = "personal-axis";
              })
              [
                "ghostspace/axis"
                "ghostspace/axis-governance"
                "ghostspace/axis-lab"
              ];
        }
        {
          id = "gitlab-ap-eks";
          host = "git.ap.org";
          planning_mode = "native";
          workflow_scheme = "work-native";
          transport = "ssh";
          ssh_host = "nyx";
          groups = [
            {
              path = "gitops/infra/eks-platform";
              logical_project = "work-eks-platform";
            }
          ];
          projects =
            map
              (path: {
                inherit path;
                logical_project = "work-eks-platform";
              })
              [
                "gitops/infra/eks-platform/eks-platform-governance"
                "gitops/infra/eks-platform/fleet-v2"
                "gitops/infra/eks-platform/infra-oci"
                "gitops/infra/eks-platform/workloads-oci"
                "gitops/infra/eks-platform/addons-oci"
              ];
        }
        {
          id = "gitlab-ap-gitlab";
          host = "git.ap.org";
          planning_mode = "native";
          workflow_scheme = "work-native";
          transport = "ssh";
          ssh_host = "nyx";
          groups = [
            {
              path = "gitops/infra/gitlab";
              logical_project = "work-gitlab";
            }
          ];
          projects =
            map
              (path: {
                inherit path;
                logical_project = "work-gitlab";
              })
              [
                "gitops/infra/gitlab/gitlab-governance"
                "gitops/infra/gitlab/gitlab-infra-tf"
              ];
        }
      ];
    };
  };

  profiles.hermesPeerDispatch = {
    enable = true;
    peers = {
      ghost = {
        inherit (peerUrls.ghost) url;
        keyFile = config.sops.secrets.hermes_mesh_api_key_ghost.path;
      };
      nyx = {
        inherit (peerUrls.nyx) url;
        keyFile = config.sops.secrets.hermes_mesh_api_key_nyx.path;
      };
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
    assistant = mkNamedMeshProfile "assistant" "claude-sonnet-4-6" baseSecretsCommand { };
    chief-of-staff = mkNamedMeshProfile "chief-of-staff" "claude-sonnet-5" chiefSecretsCommand {
      "platforms.slack.enabled" = true;
      "mcp_servers.gitlab_com" = {
        command = toString gitlabComMcp;
        connect_timeout = 30;
        timeout = 180;
      };
    };
    researcher = mkNamedMeshProfile "researcher" "kimi-k2.5" baseSecretsCommand { };
    architect = mkNamedMeshProfile "architect" "claude-opus-5" baseSecretsCommand { };
    coder = mkNamedMeshProfile "coder" "qwen3-coder-next" baseSecretsCommand { };
    tester = mkNamedMeshProfile "tester" "deepseek-v3.2" baseSecretsCommand { };
    reviewer = mkNamedMeshProfile "reviewer" "claude-sonnet-5" baseSecretsCommand { };
    ops = mkNamedMeshProfile "ops" "claude-sonnet-4-6" baseSecretsCommand { };
  };
}
