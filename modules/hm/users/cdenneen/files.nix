{
  config,
  lib,
  osConfig ? null,
  nixHostName ? null,
  pkgs,
  ...
}:

let
  tomlFormat = pkgs.formats.toml { };
  cloudflareRouteInventory = import ../../../../modules/shared/cloudflare-route-inventory.nix;
  cloudflareRouteInventoryJson = pkgs.writeText "cloudflare-route-inventory.json" (
    builtins.toJSON cloudflareRouteInventory
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
  isDarwin = pkgs.stdenv.isDarwin;
  useSharedNyxMcp = isDarwin || isNyx;
  nyxSharedMcpHost = if isNyx then "127.0.0.1" else "nyx.tail0e55.ts.net";
  nyxSharedMcpUrl = port: "http://${nyxSharedMcpHost}:${toString port}";

  writableRoots = [
    "/Users/cdenneen"
    "/home/cdenneen"
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
    if isNyx then
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
            ":project_roots" = "read";
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
            ":project_roots" = "write";
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
            ":project_roots" = "write";
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
    };

  codexProfileAttrs = {
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
    tools = [ "codex" ];
    targets = [
      ".agents/skills"
      ".opencode/skills"
    ];
  };

  home.file.".codex/AGENTS.md".source = ./ai/AGENTS.md;
  home.file.".codex/RTK.md".source = ./ai/RTK.md;

  home.file.".claude/CLAUDE.md".source = ./ai/AGENTS.md;

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
  home.file.".codex/safe-relaxed.config.toml".source =
    tomlFormat.generate "codex-safe-relaxed.config.toml"
      codexProfileAttrs."safe-relaxed";
  home.file.".codex/ci-runner.config.toml".source =
    tomlFormat.generate "codex-ci-runner.config.toml"
      codexProfileAttrs."ci-runner";
  home.file.".codex/strict.config.toml".source =
    tomlFormat.generate "codex-strict.config.toml" codexProfileAttrs.strict;

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

    seed_workspace_config() {
      local workspace_path="$1"
      local template_name="$2"
      local template="$HOME/.codex/templates/$template_name.toml"
      local dst_dir="$workspace_path/.codex"
      local dst="$dst_dir/config.toml"

      if [ ! -d "$workspace_path" ] || [ ! -f "$template" ]; then
        return 0
      fi

      $DRY_RUN_CMD mkdir -p "$dst_dir"

      if [ ! -f "$dst" ]; then
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 600 -T "$template" "$dst"
      fi
    }

    seed_workspace_config "$HOME/code/workspace/infra" infra
    seed_workspace_config "$HOME/code/workspace/eks" eks
    seed_workspace_config "$HOME/code/workspace/gitlab" gitlab
    seed_workspace_config "$HOME/src/workspace/infra" infra
    seed_workspace_config "$HOME/src/workspace/eks" eks
    seed_workspace_config "$HOME/src/workspace/gitlab" gitlab
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
