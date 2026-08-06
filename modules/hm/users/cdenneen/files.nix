{
  agentPkgs ? null,
  config,
  fluxcdAgentSkills,
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

  home.file.".agents/skills/cocoindex-code/SKILL.md".source = ./ai/skills/cocoindex-code/SKILL.md;
  home.file.".agents/skills/rtk-workflow/SKILL.md".source = ./ai/skills/rtk-workflow/SKILL.md;

  home.file.".opencode/skills/cocoindex-code/SKILL.md".source = ./ai/skills/cocoindex-code/SKILL.md;
  home.file.".opencode/skills/rtk-workflow/SKILL.md".source = ./ai/skills/rtk-workflow/SKILL.md;

  home.file.".claude/CLAUDE.md".source = ./ai/AGENTS.md;

  home.file.".claude/mcp-settings.source".text = builtins.toJSON ({
    mcpServers = {
      recallium = {
        type = "http";
        url = nyxSharedMcpUrl 18001;
      };
      context7 = {
        type = "http";
        url = nyxSharedMcpUrl 18106;
      };
      playwright = {
        type = "http";
        url = nyxSharedMcpUrl 18107;
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
  home.file.".local/bin/codex-eros" = {
    source = ./files/eros-agent;
    executable = true;
  };
  home.file.".local/bin/opencode-eros" = {
    source = ./files/eros-agent;
    executable = true;
  };
  home.file.".local/bin/claude-eros" = {
    source = ./files/eros-agent;
    executable = true;
  };
  home.file.".local/bin/hermes-eros" = {
    source = ./files/eros-agent;
    executable = true;
  };
  home.file.".local/bin/pi-eros" = {
    source = ./files/eros-agent;
    executable = true;
  };
  home.file.".pi/agent/extensions/eros.ts".text = ''
    import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

    const models = ["coding", "coding-openai", "coding-haiku", "coding-gemini", "coding-strong"];

    export default function (pi: ExtensionAPI) {
      pi.registerProvider("eros", {
        name: "Eros LiteLLM",
        baseUrl: "http://100.117.68.38:4000/v1",
        apiKey: "$EROS_LITELLM_API_KEY",
        api: "openai-completions",
        models: models.map((id) => ({
          id,
          name: `Eros ''${id}`,
          reasoning: false,
          input: ["text"],
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
          contextWindow: 32768,
          maxTokens: 4096,
        })),
      });
    }
  '';
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
