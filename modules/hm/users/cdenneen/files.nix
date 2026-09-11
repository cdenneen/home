{
  agentPkgs ? null,
  config,
  fluxcdAgentSkills,
  greptileSkills,
  lib,
  osConfig ? null,
  nixHostName ? null,
  pkgs,
  ponytail-src,
  ...
}:

let
  tomlFormat = pkgs.formats.toml { };
  cloudflareRouteInventory = import ../../../../modules/shared/cloudflare-route-inventory.nix;
  cloudflareRouteInventoryJson = pkgs.writeText "cloudflare-route-inventory.json" (
    builtins.toJSON cloudflareRouteInventory
  );
  cocoindexCodeExe = lib.getExe (pkgs.callPackage ../../../../pkgs/cocoindex-code.nix { });
  piPluginsPkg = if agentPkgs != null then agentPkgs.pi-plugins else null;
  ponytailVersion = (builtins.fromJSON (builtins.readFile "${ponytail-src}/package.json")).version;
  enableAgentPlugins = agentPkgs != null;
  piPackagePaths = [
    "${piPluginsPkg}/lib/pi-plugins/node_modules/pi-mcp-adapter"
    "${piPluginsPkg}/lib/pi-plugins/node_modules/pi-subagents"
    "${piPluginsPkg}/lib/pi-plugins/node_modules/pi-simplify"
    "${piPluginsPkg}/lib/pi-plugins/node_modules/@narumitw/pi-goal"
    "${piPluginsPkg}/lib/pi-plugins/node_modules/pi-hermes-memory"
    "${piPluginsPkg}/lib/pi-plugins/node_modules/pi-litellm"
    # Keep alternate goal implementations packaged but disabled to avoid
    # duplicate /goal command registration.
    {
      source = "${piPluginsPkg}/lib/pi-plugins/node_modules/pi-goal-list-loop-audit";
      extensions = [ ];
    }
    # pi-rtk-optimizer 0.9.0 supports Pi only through 0.80.x.
    {
      source = "${piPluginsPkg}/lib/pi-plugins/node_modules/pi-rtk-optimizer";
      extensions = [ ];
    }
    {
      source = "${piPluginsPkg}/lib/pi-plugins/node_modules/pi-codex-goal";
      extensions = [ ];
    }
    "${ponytail-src}"
  ];
  piPackagesJson = pkgs.writeText "pi-packages.json" (builtins.toJSON piPackagePaths);
  piManagedPackageSources = map (
    package: if builtins.isString package then package else package.source
  ) piPackagePaths;
  piManagedPackagesJson = pkgs.writeText "pi-managed-packages.json" (
    builtins.toJSON piManagedPackageSources
  );
  emptyJsonArray = pkgs.writeText "empty-array.json" "[]";
  piLegacyPackageSources = [
    "npm:pi-mcp-adapter"
    "npm:pi-subagents"
    "npm:pi-simplify"
    "npm:@narumitw/pi-goal"
    "npm:pi-goal-list-loop-audit"
    "npm:pi-hermes-memory"
    "npm:pi-litellm"
    "npm:pi-rtk-optimizer"
    "npm:pi-codex-goal"
    "git:github.com/DietrichGebert/ponytail"
  ];
  piLegacyPackagesJson = pkgs.writeText "pi-legacy-packages.json" (
    builtins.toJSON piLegacyPackageSources
  );
  homeDir = config.home.homeDirectory;
  hostName =
    if osConfig != null then
      (osConfig.networking.hostName or "")
    else if nixHostName != null then
      nixHostName
    else
      builtins.getEnv "HOSTNAME";
  isNyx = hostName == "nyx";
  isGhost = hostName == "ghost";
  isDarwin = pkgs.stdenv.isDarwin;
  hostSystem = pkgs.stdenv.hostPlatform.system;
  useSharedNyxMcp = isDarwin || isNyx || isGhost;
  nyxSharedMcpHost = if isNyx then "127.0.0.1" else "nyx.tail0e55.ts.net";
  nyxSharedMcpUrl = port: "http://${nyxSharedMcpHost}:${toString port}/mcp";
  graphifyMcpUrl = nyxSharedMcpUrl 18108;
  # LiteLLM /mcp gateway MVP (2026-09-11): registered on eros as mcp_servers.
  # Same 9 plain-HTTP servers as the direct nyxSharedMcpUrl entries above,
  # proxied through Eros for centralized auth/budget/tool-search. Header
  # confirmed live against v1.94.0: "Authorization: Bearer <key>" - NOT
  # "x-litellm-api-key" (that form 401s with "Malformed API Key... Ensure
  # Key has `Bearer ` prefix").
  erosLitellmMcpUrl = name: "http://eros.tail0e55.ts.net:4000/mcp/${name}/mcp";

  writableRoots = [
    "/Users/cdenneen/code/workspace"
    "/home/cdenneen/src/workspace"
    "/tmp"
    "${homeDir}/.cache"
    "${homeDir}/.cache/pip"
    "${homeDir}/.cache/uv"
    "${homeDir}/.cargo"
    "${homeDir}/.rustup"
    "${homeDir}/.yarn"
    "${homeDir}/.npm"
    "${homeDir}/.local/share/pnpm"
  ];

  mkMcpCommand = script: {
    command = "bash";
    args = [
      "-lc"
      script
    ];
  };

  mkSharedMcpCommand =
    port: script:
    if useSharedNyxMcp then
      {
        url = nyxSharedMcpUrl port;
      }
    else
      mkMcpCommand script;

  mkLocalMcpCommand = script: {
    command = "bash";
    args = [
      "-lc"
      script
    ];
  };

  mkNyxOnlySharedMcpCommand =
    port: script:
    if isNyx || isGhost then
      {
        url = nyxSharedMcpUrl port;
      }
    else
      mkLocalMcpCommand script;

  mcpGitlabScript = ''
    set -euo pipefail

    export GITLAB_API_URL="https://git.ap.org/api/v4"
    export GITLAB_READ_ONLY_MODE="true"

    if [ -z "''${GITLAB_PERSONAL_ACCESS_TOKEN:-}" ] && command -v glab >/dev/null 2>&1; then
      token="$(glab auth token -h git.ap.org 2>/dev/null || true)"
      if [ -z "$token" ]; then
        token="$(glab auth token 2>/dev/null || true)"
      fi
      if [ -n "$token" ]; then
        export GITLAB_PERSONAL_ACCESS_TOKEN="$token"
      fi
    fi

    exec npx -y @zereight/mcp-gitlab
  '';

  mcpKubernetesScript = ''
    set -euo pipefail

    kubeconfig="''${KUBECONFIG:-$HOME/.kube/config}"
    if [ -r "$kubeconfig" ]; then
      sanitized="''${TMPDIR:-/tmp}/codex-kubeconfig.$$"
      sed -E 's/^([[:space:]]*-[[:space:]]+)no([[:space:]]*)$/\1"no"\2/' "$kubeconfig" > "$sanitized"
      export KUBECONFIG="$sanitized"
    fi

    exec npx -y @strowk/mcp-k8s
  '';

  mcpAwsScript = ''
    set -euo pipefail
    export LOG_LEVEL="error"
    exec npx -y aws-mcp-readonly-lite
  '';

  mcpTerraformScript = ''
    set -euo pipefail

    if command -v podman >/dev/null 2>&1 && podman info >/dev/null 2>&1; then
      exec podman run -i --rm hashicorp/terraform-mcp-server:0.4.0
    fi

    exec npx -y terraform-mcp-server
  '';

  mcpDuckDuckGoScript = ''
    set -euo pipefail
    exec npx -y ddg-mcp-search
  '';

  mcpContext7Script = ''
    set -euo pipefail
    exec npx -y @upstash/context7-mcp
  '';

  mcpPlaywrightScript = ''
    set -euo pipefail
    exec npx -y @playwright/mcp
  '';

  codexConfigAttrs =
    (lib.optionalAttrs isDarwin {
      notify = [
        "python3"
        "${homeDir}/.codex/notify.py"
      ];
    })
    // {
      model = "gpt-5.3-codex";
      model_reasoning_effort = "xhigh";
      model_reasoning_summary = "detailed";
      personality = "none";
      file_opener = "none";
      show_raw_agent_reasoning = true;
      web_search = "live";
      history = {
        persistence = "save-all";
        max_bytes = 268435456;
      };
      agents = {
        max_threads = 6;
      };
      default_permissions = "workspace-dev";
      permissions = {
        "readonly-safe" = {
          filesystem = {
            ":minimal" = "read";
            ":workspace_roots" = "read";
            ":tmpdir" = "write";
          };
          network = {
            enabled = true;
            mode = "limited";
          };
        };
        "workspace-dev" = {
          filesystem = {
            ":minimal" = "read";
            ":workspace_roots" = "write";
            ":tmpdir" = "write";
          };
          network = {
            enabled = true;
            mode = "limited";
          };
        };
        "ci-runner" = {
          filesystem = {
            ":minimal" = "read";
            ":workspace_roots" = "write";
            ":tmpdir" = "write";
            "${homeDir}/code/workspace" = "write";
            "${homeDir}/src/workspace" = "write";
          };
          network = {
            enabled = true;
            mode = "full";
            allow_local_binding = true;
          };
        };
      };
      features = {
        child_agents_md = true;
        steer = true;
      };
      mcp_servers = {
        github = {
          url = "https://api.githubcopilot.com/mcp/";
          bearer_token_env_var = "GITHUB_TOKEN";
          required = false;
          startup_timeout_sec = 20;
          tool_timeout_sec = 120;
        };
        recallium = {
          url = nyxSharedMcpUrl 18001;
          required = false;
          startup_timeout_sec = 20;
          tool_timeout_sec = 180;
        };
        graphify = {
          url = graphifyMcpUrl;
          required = false;
          startup_timeout_sec = 30;
          tool_timeout_sec = 180;
        };
        supabase = {
          url = "https://mcp.supabase.com/mcp?project_ref=kefpmmjhtdxhhhcndrnx";
          required = false;
          startup_timeout_sec = 20;
          tool_timeout_sec = 180;
        };
        gitlab = (mkSharedMcpCommand 18101 mcpGitlabScript) // {
          required = false;
          startup_timeout_sec = 30;
          tool_timeout_sec = 180;
        };
        kubernetes = (mkSharedMcpCommand 18102 mcpKubernetesScript) // {
          required = false;
          startup_timeout_sec = 30;
          tool_timeout_sec = 180;
        };
        aws = (mkSharedMcpCommand 18103 mcpAwsScript) // {
          required = false;
          startup_timeout_sec = 30;
          tool_timeout_sec = 180;
        };
        terraform = (mkSharedMcpCommand 18104 mcpTerraformScript) // {
          required = false;
          startup_timeout_sec = 30;
          tool_timeout_sec = 240;
        };
        duckduckgo = (mkSharedMcpCommand 18105 mcpDuckDuckGoScript) // {
          required = false;
          startup_timeout_sec = 20;
          tool_timeout_sec = 120;
        };
        context7 = (mkSharedMcpCommand 18106 mcpContext7Script) // {
          required = false;
          startup_timeout_sec = 20;
          tool_timeout_sec = 120;
        };
        playwright = (mkNyxOnlySharedMcpCommand 18107 mcpPlaywrightScript) // {
          required = false;
          startup_timeout_sec = 30;
          tool_timeout_sec = 180;
        };
        cocoindex-code = {
          command = cocoindexCodeExe;
          args = [ "mcp" ];
          required = false;
          startup_timeout_sec = 30;
          tool_timeout_sec = 180;
        };
      }
      // lib.optionalAttrs isGhost {
        cloudflare = {
          command = "bash";
          args = [
            "-lc"
            ''
              set -euo pipefail
              exec npx -y @cloudflare/mcp-server-cloudflare
            ''
          ];
          env = {
            CLOUDFLARE_API_TOKEN = "{sops:cloudflare_account_api_token}";
            CLOUDFLARE_ACCOUNT_ID = "19a23ecf9ba79236ab8e64c8c7bf3507";
          };
          required = false;
          startup_timeout_sec = 20;
          tool_timeout_sec = 120;
        };
      };
      sandbox_mode = "workspace-write";
      approval_policy = "on-request";
      sandbox_workspace_write = {
        network_access = true;
        writable_roots = writableRoots;
      };
      shell_environment_policy = {
        "inherit" = "all";
        ignore_default_excludes = true;
      };
    }
    // lib.optionalAttrs enableAgentPlugins {
      marketplaces.ponytail = {
        source_type = "local";
        source = "${ponytail-src}";
      };
      plugins."ponytail@ponytail".enabled = true;
    };

  codexProfileAttrs = {
    eros = {
      model = "coding";
      model_reasoning_effort = "high";
      model_reasoning_summary = "none";
      model_provider = "eros";
      model_providers.eros = {
        name = "Eros LiteLLM";
        base_url = "http://100.117.68.38:4000/v1";
        env_key = "EROS_LITELLM_API_KEY";
        wire_api = "responses";
      };
    };
    "fast-triage" = {
      approval_policy = "on-request";
      sandbox_mode = "workspace-write";
      model_reasoning_effort = "medium";
      model_reasoning_summary = "concise";
    };
    "safe-relaxed" = {
      approval_policy = "on-request";
      sandbox_mode = "workspace-write";
      model_reasoning_effort = "xhigh";
      model_reasoning_summary = "detailed";
    };
    "ci-runner" = {
      approval_policy = "on-request";
      sandbox_mode = "workspace-write";
      model_reasoning_effort = "high";
      model_reasoning_summary = "detailed";
    };
    strict = {
      approval_policy = "untrusted";
      sandbox_mode = "workspace-write";
      model_reasoning_effort = "high";
    };
  };
in
{
  # User-scoped config files for cdenneen.
  # Keep this limited to small, self-contained files.

  home.activation.ensureAwsConfigDir = lib.hm.dag.entryBefore [ "writeBoundary" ] ''
    $DRY_RUN_CMD mkdir -p "$HOME/.aws"
  '';

  # Keep the repo-managed aws config as a store symlink, then copy it into place
  # so it can be patched on EC2 (store paths are read-only).
  home.file.".aws/config.source".source = ./files/aws-config;

  home.file.".config/opencode/AGENTS.md".source = ./ai/AGENTS.md;
  home.file.".config/opencode/docs/agent-commands.md".source = ./opencode/docs/agent-commands.md;
  home.file.".config/opencode/docs/agent-secrets.md".source = ./opencode/docs/agent-secrets.md;

  programs."fluxcd-agent-skills" = {
    enable = true;
    package = fluxcdAgentSkills.packages.${hostSystem}.skills;
    installPackage = fluxcdAgentSkills.packages.${hostSystem}.install;
    tools = [ "codex" ];
    targets = [
      ".agents/skills"
      ".opencode/skills"
    ];
  };

  home.file.".codex/AGENTS.md".source = ./ai/AGENTS.md;
  home.file.".codex/RTK.md".source = ./ai/RTK.md;
  home.file.".codex/skills/cocoindex-code/SKILL.md".source = ./ai/skills/cocoindex-code/SKILL.md;
  home.file.".codex/skills/rtk-workflow/SKILL.md".source = ./ai/skills/rtk-workflow/SKILL.md;
  home.file.".codex/skills/greploop/SKILL.md".source = "${greptileSkills}/greploop/SKILL.md";

  home.file.".agents/skills/cocoindex-code/SKILL.md".source = ./ai/skills/cocoindex-code/SKILL.md;
  home.file.".agents/skills/rtk-workflow/SKILL.md".source = ./ai/skills/rtk-workflow/SKILL.md;
  home.file.".agents/skills/greploop/SKILL.md".source = "${greptileSkills}/greploop/SKILL.md";

  home.file.".opencode/skills/cocoindex-code/SKILL.md".source = ./ai/skills/cocoindex-code/SKILL.md;
  home.file.".opencode/skills/rtk-workflow/SKILL.md".source = ./ai/skills/rtk-workflow/SKILL.md;
  home.file.".opencode/skills/greploop/SKILL.md".source = "${greptileSkills}/greploop/SKILL.md";

  home.file.".claude/CLAUDE.md".source = ./ai/AGENTS.md;
  home.file.".claude/skills/greploop/SKILL.md".source = "${greptileSkills}/greploop/SKILL.md";
  home.file.".hermes/skills/greploop/SKILL.md".source = "${greptileSkills}/greploop/SKILL.md";
  home.file.".pi/agent/skills/greploop/SKILL.md".source = "${greptileSkills}/greploop/SKILL.md";

  home.file.".codex/skills/graphify/SKILL.md".source = ./ai/skills/graphify/SKILL.md;
  home.file.".agents/skills/graphify/SKILL.md".source = ./ai/skills/graphify/SKILL.md;
  home.file.".opencode/skills/graphify/SKILL.md".source = ./ai/skills/graphify/SKILL.md;
  home.file.".claude/skills/graphify/SKILL.md".source = ./ai/skills/graphify/SKILL.md;
  home.file.".hermes/skills/graphify/SKILL.md".source = ./ai/skills/graphify/SKILL.md;
  home.file.".pi/agent/skills/graphify/SKILL.md".source = ./ai/skills/graphify/SKILL.md;

  # Symlink pi packages from the pi-plugins Nix store package to ~/.pi/agent/npm/node_modules/
  # Only create symlinks when piPluginsPkg is available (i.e., when agentPkgs is set)
  home.file.".pi/agent/npm/node_modules/pi-mcp-adapter" = lib.mkIf (piPluginsPkg != null) {
    source = "${piPluginsPkg}/lib/pi-plugins/node_modules/pi-mcp-adapter";
  };
  home.file.".pi/agent/npm/node_modules/pi-subagents" = lib.mkIf (piPluginsPkg != null) {
    source = "${piPluginsPkg}/lib/pi-plugins/node_modules/pi-subagents";
  };
  home.file.".pi/agent/npm/node_modules/pi-simplify" = lib.mkIf (piPluginsPkg != null) {
    source = "${piPluginsPkg}/lib/pi-plugins/node_modules/pi-simplify";
  };
  home.file.".pi/agent/npm/node_modules/@narumitw/pi-goal" = lib.mkIf (piPluginsPkg != null) {
    source = "${piPluginsPkg}/lib/pi-plugins/node_modules/@narumitw/pi-goal";
  };
  home.file.".pi/agent/npm/node_modules/pi-hermes-memory" = lib.mkIf (piPluginsPkg != null) {
    source = "${piPluginsPkg}/lib/pi-plugins/node_modules/pi-hermes-memory";
  };
  home.file.".pi/agent/npm/node_modules/pi-litellm" = lib.mkIf (piPluginsPkg != null) {
    source = "${piPluginsPkg}/lib/pi-plugins/node_modules/pi-litellm";
  };
  home.file.".pi/agent/npm/node_modules/pi-goal-list-loop-audit" = lib.mkIf (piPluginsPkg != null) {
    source = "${piPluginsPkg}/lib/pi-plugins/node_modules/pi-goal-list-loop-audit";
  };
  home.file.".pi/agent/npm/node_modules/pi-rtk-optimizer" = lib.mkIf (piPluginsPkg != null) {
    source = "${piPluginsPkg}/lib/pi-plugins/node_modules/pi-rtk-optimizer";
  };
  home.file.".pi/agent/npm/node_modules/pi-codex-goal" = lib.mkIf (piPluginsPkg != null) {
    source = "${piPluginsPkg}/lib/pi-plugins/node_modules/pi-codex-goal";
  };

  # The LiteLLM key must be rendered from SOPS: pi-litellm reads this file
  # directly, before pi's normal $ENV_VAR interpolation path.
  #
  # modelOverrides come from the eros LiteLLM proxy's own /model/info
  # (max_output_tokens/max_input_tokens per alias), not guesses -- pi's
  # default keyword-based maxTokens inference (pi-litellm's litellm-sync.ts)
  # is wrong for generic proxy aliases like "coding-strong".
  home.file.".pi/agent/models.json.tmpl".text = builtins.toJSON {
    providers.litellm = {
      baseUrl = null;
      api = "openai-completions";
      apiKey = null;
      modelOverrides = {
        coding-strong = {
          maxTokens = 128000;
          contextWindow = 1000000;
        };
        coding-core = {
          maxTokens = 8192;
          contextWindow = 262144;
        };
        coding = {
          maxTokens = 4096;
          contextWindow = 28672;
        };
        coding-openai = {
          maxTokens = 128000;
          contextWindow = 272000;
        };
        coding-gemini = {
          maxTokens = 65535;
          contextWindow = 1048576;
        };
        coding-haiku = {
          maxTokens = 64000;
          contextWindow = 200000;
        };
        review-strong = {
          maxTokens = 128000;
          contextWindow = 200000;
        };
        general-core = {
          maxTokens = 8192;
          contextWindow = 128000;
        };
        multimodal-long = {
          maxTokens = 64000;
          contextWindow = 1000000;
        };
        research-candidate = {
          maxTokens = 262144;
          contextWindow = 262144;
        };
        reasoning-candidate = {
          maxTokens = 163840;
          contextWindow = 163840;
        };
      };
    };
  };

  home.file.".claude/mcp-settings.source".text = builtins.toJSON ({
    mcpServers = {
      # MVP set (2026-09-11): routed through Eros LiteLLM's /mcp gateway
      # instead of directly at nyx, for centralized auth/budget/tool-search.
      # Placeholder substituted with the real eros-claude-clients key by
      # claudeMcpSettingsWrite below.
      recallium = {
        type = "http";
        url = erosLitellmMcpUrl "recallium";
        headers = { Authorization = "__EROS_CLAUDE_CLIENTS_KEY_PLACEHOLDER__"; };
      };
      graphify = {
        type = "http";
        url = erosLitellmMcpUrl "graphify";
        headers = { Authorization = "__EROS_CLAUDE_CLIENTS_KEY_PLACEHOLDER__"; };
      };
      context7 = {
        type = "http";
        url = erosLitellmMcpUrl "context7";
        headers = { Authorization = "__EROS_CLAUDE_CLIENTS_KEY_PLACEHOLDER__"; };
      };
      playwright = {
        type = "http";
        url = erosLitellmMcpUrl "playwright";
        headers = { Authorization = "__EROS_CLAUDE_CLIENTS_KEY_PLACEHOLDER__"; };
      };
      kubernetes = {
        type = "http";
        url = erosLitellmMcpUrl "kubernetes";
        headers = { Authorization = "__EROS_CLAUDE_CLIENTS_KEY_PLACEHOLDER__"; };
      };
      aws = {
        type = "http";
        url = erosLitellmMcpUrl "aws";
        headers = { Authorization = "__EROS_CLAUDE_CLIENTS_KEY_PLACEHOLDER__"; };
      };
      terraform = {
        type = "http";
        url = erosLitellmMcpUrl "terraform";
        headers = { Authorization = "__EROS_CLAUDE_CLIENTS_KEY_PLACEHOLDER__"; };
      };
      duckduckgo = {
        type = "http";
        url = erosLitellmMcpUrl "duckduckgo";
        headers = { Authorization = "__EROS_CLAUDE_CLIENTS_KEY_PLACEHOLDER__"; };
      };
      gitlab = {
        type = "http";
        url = erosLitellmMcpUrl "gitlab";
        headers = { Authorization = "__EROS_CLAUDE_CLIENTS_KEY_PLACEHOLDER__"; };
      };
      cocoindex-code = {
        command = cocoindexCodeExe;
        args = [ "mcp" ];
      };
    }
    // lib.optionalAttrs isGhost {
      cloudflare = {
        type = "http";
        url = "https://mcp.cloudflare.com/mcp";
        headers = {
          Authorization = "__CLOUDFLARE_API_TOKEN_PLACEHOLDER__";
        };
      };
    };
  });

  home.file.".codex/subagents/kubernetes-expert.md".source = ./ai/subagents/kubernetes-expert.md;
  home.file.".codex/subagents/terraform-expert.md".source = ./ai/subagents/terraform-expert.md;
  home.file.".codex/subagents/gitlab-ci-expert.md".source = ./ai/subagents/gitlab-ci-expert.md;
  home.file.".codex/subagents/aws-expert.md".source = ./ai/subagents/aws-expert.md;
  home.file.".codex/subagents/nix-expert.md".source = ./ai/subagents/nix-expert.md;
  home.file.".codex/subagents/flux-expert.md".source = ./ai/subagents/flux-expert.md;
  home.file.".codex/agents/kubernetes-expert.toml".source = ./ai/agents/kubernetes-expert.toml;
  home.file.".codex/agents/terraform-expert.toml".source = ./ai/agents/terraform-expert.toml;
  home.file.".codex/agents/gitlab-ci-expert.toml".source = ./ai/agents/gitlab-ci-expert.toml;
  home.file.".codex/agents/aws-expert.toml".source = ./ai/agents/aws-expert.toml;
  home.file.".codex/agents/nix-expert.toml".source = ./ai/agents/nix-expert.toml;
  home.file.".codex/agents/flux-expert.toml".source = ./ai/agents/flux-expert.toml;
  home.file.".codex/templates/infra.toml".source = ./ai/workspace-templates/infra.toml;
  home.file.".codex/templates/eks.toml".source = ./ai/workspace-templates/eks.toml;
  home.file.".codex/templates/gitlab.toml".source = ./ai/workspace-templates/gitlab.toml;
  home.file.".codex/notify.py" = {
    source = ./ai/notify.py;
    executable = true;
  };
  home.file.".local/bin/restart-tmux" = {
    source = ./files/restart-tmux;
    executable = true;
  };
  home.file.".local/bin/ivanti-reset" = {
    source = ./files/ivanti-reset;
    executable = true;
  };
  home.file.".local/bin/ensure-oci-ghost-runner" = {
    source = ./files/ensure-oci-ghost-runner;
    executable = true;
  };
  home.file.".local/bin/ensure-peps-runner" = {
    source = ./files/ensure-peps-runner;
    executable = true;
  };
  home.file.".local/bin/deploy-app" = {
    source = ./files/deploy-app;
    executable = true;
  };
  home.file.".local/bin/cf-move-routes" = {
    source = ./files/cf-move-routes;
    executable = true;
  };
  home.file.".local/bin/cf-move-published-routes" = {
    source = ./files/cf-move-published-routes;
    executable = true;
  };
  home.file.".config/cloudflare/route-inventory.json".source = cloudflareRouteInventoryJson;
  home.file.".local/bin/nyx-mcp-preflight" = {
    source = ./files/nyx-mcp-preflight;
    executable = true;
  };
  home.file.".local/bin/nyx-mcp-status" = {
    source = ./files/nyx-mcp-status;
    executable = true;
  };
  home.file.".local/bin/opencode-attach-latest" = {
    source = ./files/opencode-attach-latest;
    executable = true;
  };
  home.file.".codex/config.toml.source".source =
    tomlFormat.generate "codex-config.toml" codexConfigAttrs;
  home.file.".codex/fast-triage.config.toml".source =
    tomlFormat.generate "codex-fast-triage.config.toml"
      codexProfileAttrs."fast-triage";
  home.file.".codex/eros.config.toml".source =
    tomlFormat.generate "codex-eros.toml" codexProfileAttrs.eros;
  home.file.".codex/safe-relaxed.config.toml".source =
    tomlFormat.generate "codex-safe-relaxed.config.toml"
      codexProfileAttrs."safe-relaxed";
  home.file.".codex/ci-runner.config.toml".source =
    tomlFormat.generate "codex-ci-runner.config.toml"
      codexProfileAttrs."ci-runner";
  home.file.".codex/strict.config.toml".source =
    tomlFormat.generate "codex-strict.config.toml" codexProfileAttrs.strict;

  home.activation.claudeMcpSettingsWrite = lib.hm.dag.entryAfter [ "linkGeneration" ] ''
    set -euo pipefail

    mcp_src="$HOME/.claude/mcp-settings.source"
    dst="$HOME/.claude.json"

    if [ ! -f "$mcp_src" ]; then
      exit 0
    fi

    mcp_json="$(${pkgs.coreutils}/bin/cat "$mcp_src")"

    # Substitute cloudflare API token placeholder at activation time so the
    # secret never lands in the nix store.
    cf_token=""
    for _cf_candidate in \
      /run/user/1000/secrets.d/*/cloudflare_account_api_token \
      "$HOME/.local/share/sops-nix/secrets/cloudflare_account_api_token" \
      "$HOME/.config/sops-nix/secrets/cloudflare_account_api_token"
    do
      if [ -r "$_cf_candidate" ]; then
        cf_token="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "$_cf_candidate")"
        break
      fi
    done

    if [ -n "$cf_token" ]; then
      mcp_json="$(printf '%s' "$mcp_json" | \
        ${pkgs.gnused}/bin/sed "s|__CLOUDFLARE_API_TOKEN_PLACEHOLDER__|Bearer $cf_token|g")"
    fi

    # Substitute the eros-claude-clients LiteLLM key for the MCP gateway
    # entries at activation time, same pattern as cf_token above.
    eros_key=""
    for _eros_candidate in \
      /run/user/1000/secrets.d/*/eros_litellm_key_claude_clients \
      "$HOME/.local/share/sops-nix/secrets/eros_litellm_key_claude_clients" \
      "$HOME/.config/sops-nix/secrets/eros_litellm_key_claude_clients"
    do
      if [ -r "$_eros_candidate" ]; then
        eros_key="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "$_eros_candidate")"
        break
      fi
    done

    if [ -n "$eros_key" ]; then
      mcp_json="$(printf '%s' "$mcp_json" | \
        ${pkgs.gnused}/bin/sed "s|__EROS_CLAUDE_CLIENTS_KEY_PLACEHOLDER__|Bearer $eros_key|g")"
    fi

    if [ -f "$dst" ]; then
      merged="$(printf '%s' "$mcp_json" | ${pkgs.jq}/bin/jq -s '.[0] + {mcpServers: .[1].mcpServers}' "$dst" -)"
    else
      merged="$mcp_json"
    fi

    tmp="$(${pkgs.coreutils}/bin/mktemp "$HOME/.claude.json.XXXXXX")"
    printf '%s\n' "$merged" > "$tmp"
    $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 600 -T "$tmp" "$dst"
    $DRY_RUN_CMD ${pkgs.coreutils}/bin/rm -f "$tmp"
  '';

  home.activation.ponytailPluginCache = lib.mkIf enableAgentPlugins (
    lib.hm.dag.entryAfter [ "linkGeneration" ] ''
      set -euo pipefail

      for cache_dir in \
        "$HOME/.codex/plugins/cache/ponytail/ponytail/${ponytailVersion}" \
        "$HOME/.claude/plugins/cache/ponytail/ponytail/${ponytailVersion}"
      do
        if [ -e "$cache_dir" ] || [ -L "$cache_dir" ]; then
          $DRY_RUN_CMD rm -rf "$cache_dir"
        fi
        $DRY_RUN_CMD mkdir -p "$cache_dir"
        $DRY_RUN_CMD ${pkgs.xorg.lndir}/bin/lndir -silent "${ponytail-src}" "$cache_dir"
      done
    ''
  );

  home.activation.ponytailPluginState = lib.mkIf enableAgentPlugins (
    lib.hm.dag.entryAfter [ "ponytailPluginCache" ] ''
      set -euo pipefail

      if [ -z "''${DRY_RUN_CMD:-}" ]; then

      claude_dir="$HOME/.claude"
      claude_settings="$claude_dir/settings.json"
      claude_plugins="$claude_dir/plugins/installed_plugins.json"
      claude_marketplaces="$claude_dir/plugins/known_marketplaces.json"
      claude_install_path="$claude_dir/plugins/cache/ponytail/ponytail/${ponytailVersion}"
      mkdir -p "$claude_dir/plugins"

      if [ -f "$claude_settings" ]; then
        claude_settings_json="$(${pkgs.coreutils}/bin/cat "$claude_settings")"
      else
        claude_settings_json='{}'
      fi

      settings_tmp="$(${pkgs.coreutils}/bin/mktemp "$claude_dir/settings.json.XXXXXX")"
      printf '%s' "$claude_settings_json" | ${pkgs.jq}/bin/jq \
        --arg source "${ponytail-src}" \
        '.extraKnownMarketplaces.ponytail.source = {source: "directory", path: $source}
         | .enabledPlugins["ponytail@ponytail"] = true' > "$settings_tmp"
      $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 600 -T "$settings_tmp" "$claude_settings"
      $DRY_RUN_CMD ${pkgs.coreutils}/bin/rm -f "$settings_tmp"

      if [ -f "$claude_plugins" ]; then
        claude_plugins_json="$(${pkgs.coreutils}/bin/cat "$claude_plugins")"
      else
        claude_plugins_json='{"version":2,"plugins":{}}'
      fi

      plugins_tmp="$(${pkgs.coreutils}/bin/mktemp "$claude_dir/plugins/installed_plugins.json.XXXXXX")"
      printf '%s' "$claude_plugins_json" | ${pkgs.jq}/bin/jq \
        --arg installPath "$claude_install_path" \
        --arg version "${ponytailVersion}" \
        '(.plugins["ponytail@ponytail"][0] // {}) as $old
         | .version = 2
         | .plugins["ponytail@ponytail"] = [{
             scope: "user",
             installPath: $installPath,
             version: $version,
             installedAt: ($old.installedAt // "1970-01-01T00:00:00.000Z"),
             lastUpdated: "1970-01-01T00:00:00.000Z"
           }]' > "$plugins_tmp"
      $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 600 -T "$plugins_tmp" "$claude_plugins"
      $DRY_RUN_CMD ${pkgs.coreutils}/bin/rm -f "$plugins_tmp"

      if [ -f "$claude_marketplaces" ]; then
        claude_marketplaces_json="$(${pkgs.coreutils}/bin/cat "$claude_marketplaces")"
      else
        claude_marketplaces_json='{}'
      fi

      marketplaces_tmp="$(${pkgs.coreutils}/bin/mktemp "$claude_dir/plugins/known_marketplaces.json.XXXXXX")"
      printf '%s' "$claude_marketplaces_json" | ${pkgs.jq}/bin/jq \
        --arg source "${ponytail-src}" \
        '.ponytail = {
           source: {source: "directory", path: $source},
           installLocation: $source,
           lastUpdated: "1970-01-01T00:00:00.000Z"
         }' > "$marketplaces_tmp"
      $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 600 -T "$marketplaces_tmp" "$claude_marketplaces"
      $DRY_RUN_CMD ${pkgs.coreutils}/bin/rm -f "$marketplaces_tmp"
      else
        echo "Would update Claude Ponytail plugin state"
      fi
    ''
  );

  # Claude Code env: point at Eros LiteLLM instead of direct Bedrock
  # (2026-09-11). Ordered after ponytailPluginState since both read-modify-
  # write ~/.claude/settings.json - real clobber risk otherwise. del() on an
  # already-absent key is a no-op, so this is safe on hosts whose env block
  # never had AWS_PROFILE/model overrides (e.g. nyx) as well as hosts that did.
  home.activation.claudeSettingsEnvWrite = lib.hm.dag.entryAfter [
    "ponytailPluginState"
    (if isDarwin then "materializeDarwinSopsSecrets" else "materializeLinuxSopsSecrets")
  ] ''
    set -euo pipefail

    claude_dir="$HOME/.claude"
    settings="$claude_dir/settings.json"
    mkdir -p "$claude_dir"

    token=""
    for _c in \
      /run/user/1000/secrets.d/*/eros_litellm_key_claude_clients \
      "$HOME/.local/share/sops-nix/secrets/eros_litellm_key_claude_clients" \
      "$HOME/.config/sops-nix/secrets/eros_litellm_key_claude_clients"
    do
      if [ -r "$_c" ]; then
        token="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "$_c")"
        break
      fi
    done

    if [ -z "$token" ]; then
      echo "warning: eros_litellm_key_claude_clients missing, skipping Claude Code env rewrite" >&2
      exit 0
    fi

    if [ -f "$settings" ]; then
      settings_json="$(${pkgs.coreutils}/bin/cat "$settings")"
    else
      settings_json='{}'
    fi

    # One-time backup before the first destructive env rewrite, so rollback
    # is a straight file copy rather than a re-derivation.
    backup="$claude_dir/settings.json.pre-eros-migration.bak"
    if [ ! -e "$backup" ] && [ -f "$settings" ]; then
      $DRY_RUN_CMD ${pkgs.coreutils}/bin/cp "$settings" "$backup"
    fi

    tmp="$(${pkgs.coreutils}/bin/mktemp "$claude_dir/settings.json.XXXXXX")"
    printf '%s' "$settings_json" | ${pkgs.jq}/bin/jq \
      --arg baseUrl "http://eros.tail0e55.ts.net:4000" \
      --arg token "$token" \
      '.env.ANTHROPIC_BASE_URL = $baseUrl
       | .env.ANTHROPIC_AUTH_TOKEN = $token
       | .env.CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY = "1"
       | del(.env.CLAUDE_CODE_USE_BEDROCK)
       | del(.env.AWS_PROFILE)
       | del(.env.AWS_REGION)
       | del(.env.ANTHROPIC_DEFAULT_HAIKU_MODEL)
       | del(.env.ANTHROPIC_DEFAULT_SONNET_MODEL)
       | del(.env.ANTHROPIC_DEFAULT_OPUS_MODEL)' > "$tmp"
    $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 600 -T "$tmp" "$settings"
    $DRY_RUN_CMD ${pkgs.coreutils}/bin/rm -f "$tmp"
  '';

  home.file.".claude-desktop-mcp-settings.source" = lib.mkIf isDarwin {
    text = builtins.toJSON {
      mcpServers = {
        recallium = {
          command = "npx";
          args = [ "-y" "mcp-remote" (erosLitellmMcpUrl "recallium") "--header" "Authorization: __EROS_CLAUDE_CLIENTS_KEY_PLACEHOLDER__" ];
        };
        graphify = {
          command = "npx";
          args = [ "-y" "mcp-remote" (erosLitellmMcpUrl "graphify") "--header" "Authorization: __EROS_CLAUDE_CLIENTS_KEY_PLACEHOLDER__" ];
        };
        context7 = {
          command = "npx";
          args = [ "-y" "mcp-remote" (erosLitellmMcpUrl "context7") "--header" "Authorization: __EROS_CLAUDE_CLIENTS_KEY_PLACEHOLDER__" ];
        };
        playwright = {
          command = "npx";
          args = [ "-y" "mcp-remote" (erosLitellmMcpUrl "playwright") "--header" "Authorization: __EROS_CLAUDE_CLIENTS_KEY_PLACEHOLDER__" ];
        };
        kubernetes = {
          command = "npx";
          args = [ "-y" "mcp-remote" (erosLitellmMcpUrl "kubernetes") "--header" "Authorization: __EROS_CLAUDE_CLIENTS_KEY_PLACEHOLDER__" ];
        };
        aws = {
          command = "npx";
          args = [ "-y" "mcp-remote" (erosLitellmMcpUrl "aws") "--header" "Authorization: __EROS_CLAUDE_CLIENTS_KEY_PLACEHOLDER__" ];
        };
        terraform = {
          command = "npx";
          args = [ "-y" "mcp-remote" (erosLitellmMcpUrl "terraform") "--header" "Authorization: __EROS_CLAUDE_CLIENTS_KEY_PLACEHOLDER__" ];
        };
        duckduckgo = {
          command = "npx";
          args = [ "-y" "mcp-remote" (erosLitellmMcpUrl "duckduckgo") "--header" "Authorization: __EROS_CLAUDE_CLIENTS_KEY_PLACEHOLDER__" ];
        };
        gitlab = {
          command = "npx";
          args = [ "-y" "mcp-remote" (erosLitellmMcpUrl "gitlab") "--header" "Authorization: __EROS_CLAUDE_CLIENTS_KEY_PLACEHOLDER__" ];
        };
      };
    };
  };

  home.activation.claudeDesktopMcpSettingsWrite = lib.mkIf isDarwin (
    lib.hm.dag.entryAfter [
      "materializeDarwinSopsSecrets"
    ] ''
      set -euo pipefail

      mcp_src="$HOME/.claude-desktop-mcp-settings.source"
      dst="$HOME/Library/Application Support/Claude/claude_desktop_config.json"

      if [ ! -f "$mcp_src" ]; then
        exit 0
      fi
      mkdir -p "$HOME/Library/Application Support/Claude"

      mcp_json="$(${pkgs.coreutils}/bin/cat "$mcp_src")"

      eros_key=""
      for _eros_candidate in \
        /run/user/1000/secrets.d/*/eros_litellm_key_claude_clients \
        "$HOME/.local/share/sops-nix/secrets/eros_litellm_key_claude_clients" \
        "$HOME/.config/sops-nix/secrets/eros_litellm_key_claude_clients"
      do
        if [ -r "$_eros_candidate" ]; then
          eros_key="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "$_eros_candidate")"
          break
        fi
      done

      if [ -z "$eros_key" ]; then
        echo "warning: eros_litellm_key_claude_clients missing, skipping Claude Desktop MCP rewrite" >&2
        exit 0
      fi

      mcp_json="$(printf '%s' "$mcp_json" | \
        ${pkgs.gnused}/bin/sed "s|__EROS_CLAUDE_CLIENTS_KEY_PLACEHOLDER__|Bearer $eros_key|g")"

      if [ -f "$dst" ]; then
        merged="$(printf '%s' "$mcp_json" | ${pkgs.jq}/bin/jq -s '.[1] + {mcpServers: .[0].mcpServers}' - "$dst")"
      else
        merged="$mcp_json"
      fi

      tmp="$(${pkgs.coreutils}/bin/mktemp "$HOME/Library/Application Support/Claude/claude_desktop_config.json.XXXXXX")"
      printf '%s\n' "$merged" > "$tmp"
      $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 600 -T "$tmp" "$dst"
      $DRY_RUN_CMD ${pkgs.coreutils}/bin/rm -f "$tmp"
    ''
  );

  home.activation.piSettingsWrite = lib.mkIf enableAgentPlugins (
    lib.hm.dag.entryAfter [ "linkGeneration" ] ''
        set -euo pipefail

        if [ -z "''${DRY_RUN_CMD:-}" ]; then

      pi_dir="$HOME/.pi/agent"
      settings="$pi_dir/settings.json"
      managed_settings="$pi_dir/.nix-managed-packages.json"
      mkdir -p "$pi_dir"

        if [ -f "$settings" ]; then
          settings_json="$(${pkgs.coreutils}/bin/cat "$settings")"
      else
        settings_json='{}'
      fi

      if [ -f "$managed_settings" ]; then
        previous_managed="$managed_settings"
      else
        previous_managed="${emptyJsonArray}"
      fi

      tmp="$(${pkgs.coreutils}/bin/mktemp "$pi_dir/settings.json.XXXXXX")"
      printf '%s' "$settings_json" | ${pkgs.jq}/bin/jq \
        --slurpfile packages "${piPackagesJson}" \
        --slurpfile managed "${piManagedPackagesJson}" \
        --slurpfile previous "$previous_managed" \
        --slurpfile legacy "${piLegacyPackagesJson}" \
        'def package_source: if type == "string" then . else .source end;
         (($managed[0] + $previous[0]) | unique) as $managed_sources
         | .packages = (
             [(.packages // [])[]
              | select((package_source as $source
                | (($managed_sources | index($source)) == null)
                  and (($legacy[0] | index($source)) == null)))]
             + $packages[0]
           )' > "$tmp"
      $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 600 -T "$tmp" "$settings"
      $DRY_RUN_CMD ${pkgs.coreutils}/bin/rm -f "$tmp"
      $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 600 -T "${piManagedPackagesJson}" "$managed_settings"
        else
          echo "Would update Pi package settings"
        fi
    ''
  );

  home.activation.piModelsWrite = lib.mkIf (config.sops.secrets ? eros_litellm_api_key) (
    lib.hm.dag.entryAfter
      [
        (if isDarwin then "materializeDarwinSopsSecrets" else "materializeLinuxSopsSecrets")
      ]
      ''
        set -euo pipefail

        template="$HOME/.pi/agent/models.json.tmpl"
        secret="${config.sops.secrets.eros_litellm_api_key.path}"
        dst="$HOME/.pi/agent/models.json"

        if [ -n "''${DRY_RUN_CMD:-}" ]; then
          echo "Would render $dst from SOPS"
        else
          if [ ! -s "$template" ]; then
            echo "Missing pi model template: $template" >&2
            exit 1
          fi
          if [ ! -s "$secret" ]; then
            echo "Missing Eros LiteLLM SOPS secret: $secret" >&2
            exit 1
          fi

          tmp="$(${pkgs.coreutils}/bin/mktemp "$HOME/.pi/agent/models.json.XXXXXX")"
          ${pkgs.jq}/bin/jq \
            --arg baseUrl "http://100.117.68.38:4000/v1" \
            --rawfile apiKey "$secret" \
            '($apiKey | sub("[\\r\\n]+$"; "")) as $key
             | if $key == "" then error("empty Eros LiteLLM key")
               else .providers.litellm.baseUrl = $baseUrl
               | .providers.litellm.apiKey = $key
               end' \
            "$template" > "$tmp"
          ${pkgs.coreutils}/bin/install -m 600 -T "$tmp" "$dst"
          ${pkgs.coreutils}/bin/rm -f "$tmp"
        fi
      ''
  );

  home.activation.piMcpConfigWrite = lib.hm.dag.entryAfter [ "linkGeneration" ] ''
    set -euo pipefail

    dst="$HOME/.pi/agent/mcp.json"
    $DRY_RUN_CMD mkdir -p "$HOME/.pi/agent"
    if [ -f "$dst" ]; then
      current="$(${pkgs.coreutils}/bin/cat "$dst")"
    else
      current='{}'
    fi

    tmp="$(${pkgs.coreutils}/bin/mktemp "$HOME/.pi/agent/mcp.json.XXXXXX")"
    printf '%s' "$current" | ${pkgs.jq}/bin/jq \
      --arg recalliumUrl ${lib.escapeShellArg (nyxSharedMcpUrl 18001)} \
      --arg graphifyUrl ${lib.escapeShellArg graphifyMcpUrl} \
      '.mcpServers.recallium = {type: "http", url: $recalliumUrl, directTools: true}
       | .mcpServers.graphify = {type: "http", url: $graphifyUrl, directTools: true}' > "$tmp"
    $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 600 -T "$tmp" "$dst"
    $DRY_RUN_CMD ${pkgs.coreutils}/bin/rm -f "$tmp"
  '';

  home.activation.graphifyHermesConfig =
    lib.hm.dag.entryAfter
      [
        "gitlabMcpProxyHermesConfig"
        "hermesGatewayBootstrapConfig"
      ]
      ''
        configure_graphify() {
          local hermes_config="$1"
          if [ -f "$hermes_config" ] && [ -z "''${DRY_RUN_CMD:-}" ]; then
            local tmp
            tmp="$(${pkgs.coreutils}/bin/mktemp --tmpdir hermes-graphify.XXXXXX)"
            ${pkgs.yq-go}/bin/yq '
              .mcp_servers.graphify.url = "${graphifyMcpUrl}"
              | .mcp_servers.graphify.timeout = 180
              | .mcp_servers.graphify.connect_timeout = 30
            ' "$hermes_config" > "$tmp"
            if ! ${pkgs.diffutils}/bin/cmp -s "$tmp" "$hermes_config" \
              || [ "$(${pkgs.coreutils}/bin/stat -c %a "$hermes_config")" != 600 ]; then
              ${pkgs.coreutils}/bin/install -m 600 -T "$tmp" "$hermes_config"
            fi
            ${pkgs.coreutils}/bin/rm -f "$tmp"
          fi
        }

        configure_graphify "$HOME/.hermes/config.yaml"
        ${lib.optionalString config.profiles.hermesGatewaySecondary.enable ''
          configure_graphify "$HOME/.hermes/profiles/${config.profiles.hermesGatewaySecondary.profileName}/config.yaml"
        ''}
      '';

  home.activation.codexConfigWrite = lib.hm.dag.entryAfter [ "linkGeneration" ] ''
    set -euo pipefail

    src="$HOME/.codex/config.toml.source"
    dst="$HOME/.codex/config.toml"

    if [ -f "$src" ]; then
      $DRY_RUN_CMD mkdir -p "$HOME/.codex"

      if [ -L "$dst" ]; then
        $DRY_RUN_CMD rm -f "$dst"
      fi

      $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 600 -T "$src" "$dst"
    fi
  '';

  home.activation.codexWorkspaceConfigSeed = lib.hm.dag.entryAfter [ "codexConfigWrite" ] ''
    set -euo pipefail

    write_workspace_config() {
      local workspace_path="$1"
      local template_name="$2"
      local template="$HOME/.codex/templates/$template_name.toml"
      local dst_dir="$workspace_path/.codex"
      local dst="$dst_dir/config.toml"

      if [ ! -d "$workspace_path" ] || [ ! -f "$template" ]; then
        return 0
      fi

      $DRY_RUN_CMD mkdir -p "$dst_dir"

      # These workspace configs are fully managed by this flake so policy
      # changes reach existing workspaces instead of only new ones.
      $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 600 -T "$template" "$dst"
    }

    write_workspace_config "$HOME/code/workspace/infra" infra
    write_workspace_config "$HOME/code/workspace/eks" eks
    write_workspace_config "$HOME/code/workspace/gitlab" gitlab
    write_workspace_config "$HOME/src/workspace/infra" infra
    write_workspace_config "$HOME/src/workspace/eks" eks
    write_workspace_config "$HOME/src/workspace/gitlab" gitlab
  '';

  home.activation.awsConfigWrite = lib.hm.dag.entryAfter [ "linkGeneration" ] ''
    set -euo pipefail

    # Always overwrite ~/.aws/config from the repo-managed source so changes to
    # the source file are reflected on next activation.
    if [ -f "$HOME/.aws/config.source" ]; then
      $DRY_RUN_CMD mkdir -p "$HOME/.aws"
      $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 600 -T \
        "$HOME/.aws/config.source" \
        "$HOME/.aws/config"
    fi
  '';

  home.activation.awsConfigEc2Patch = lib.hm.dag.entryAfter [ "awsConfigWrite" ] ''
    set -euo pipefail

    if [ ! -f "$HOME/.aws/config" ]; then
      exit 0
    fi

    if [ -r /sys/devices/virtual/dmi/id/sys_vendor ] && ${pkgs.gnugrep}/bin/grep -qi "amazon" /sys/devices/virtual/dmi/id/sys_vendor; then
      # On EC2/Cloud9, SSO profiles don't work; use instance metadata instead.
      $DRY_RUN_CMD ${pkgs.gnused}/bin/sed -i \
        -e 's/source_profile[[:space:]]*=[[:space:]]*sso-apss/credential_source = Ec2InstanceMetadata/g' \
        "$HOME/.aws/config" || true
    fi
  '';

  home.file.".kube/switch-config.yaml".source = ./switch-config.yaml;
}
