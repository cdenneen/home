{
  config,
  lib,
  pkgs,
  ...
}:

let
  tomlFormat = pkgs.formats.toml { };
  homeDir = config.home.homeDirectory;
  isDarwin = pkgs.stdenv.isDarwin;

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

  codexConfigAttrs =
    (lib.optionalAttrs isDarwin {
      notify = [
        "python3"
        "${homeDir}/.codex/notify.py"
      ];
    })
    // {
      profile = "safe-relaxed";
      model = "gpt-5.3-codex";
      model_reasoning_effort = "xhigh";
      model_reasoning_summary = "detailed";
      personality = "none";
      file_opener = "none";
      show_raw_agent_reasoning = true;
      web_search = "live";
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
      profiles = {
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
      features = {
        child_agents_md = true;
        steer = true;
      };
      mcp_servers = {
        github = {
          url = "https://api.githubcopilot.com/mcp/";
          bearer_token_env_var = "GITHUB_TOKEN";
          required = false;
          startup_timeout_sec = 12;
          tool_timeout_sec = 60;
        };
        gitlab = {
          command = "bash";
          args = [
            "-lc"
            ''
              set -euo pipefail

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
            ''
          ];
          required = false;
          startup_timeout_sec = 20;
          tool_timeout_sec = 120;
          env = {
            GITLAB_API_URL = "https://git.ap.org/api/v4";
            GITLAB_READ_ONLY_MODE = "true";
          };
          env_vars = [
            "GITLAB_PERSONAL_ACCESS_TOKEN"
          ];
        };
        kubernetes = {
          command = "bash";
          args = [
            "-lc"
            ''
              set -euo pipefail

              kubeconfig="''${KUBECONFIG:-$HOME/.kube/config}"
              if [ -r "$kubeconfig" ]; then
                sanitized="''${TMPDIR:-/tmp}/codex-kubeconfig.$$"
                sed -E 's/^([[:space:]]*-[[:space:]]+)no([[:space:]]*)$/\1"no"\2/' "$kubeconfig" > "$sanitized"
                export KUBECONFIG="$sanitized"
              fi

              exec npx -y @strowk/mcp-k8s
            ''
          ];
          required = false;
          startup_timeout_sec = 20;
          tool_timeout_sec = 120;
        };
        aws = {
          command = "npx";
          args = [
            "-y"
            "aws-mcp-readonly-lite"
          ];
          required = false;
          startup_timeout_sec = 20;
          tool_timeout_sec = 120;
          env = {
            LOG_LEVEL = "error";
          };
        };
        terraform = {
          command = "bash";
          args = [
            "-lc"
            ''
              set -euo pipefail

              if command -v podman >/dev/null 2>&1; then
                exec podman run -i --rm hashicorp/terraform-mcp-server:0.4.0
              fi

              exec npx -y terraform-mcp-server
            ''
          ];
          required = false;
          startup_timeout_sec = 25;
          tool_timeout_sec = 180;
          env_vars = [
            "TF_TOKEN_app_terraform_io"
            "TERRAFORM_TOKEN"
            "TFE_TOKEN"
          ];
        };
        duckduckgo = {
          command = "npx";
          args = [
            "-y"
            "ddg-mcp-search"
          ];
          required = false;
          startup_timeout_sec = 10;
          tool_timeout_sec = 45;
        };
        context7 = {
          command = "npx";
          args = [
            "-y"
            "@upstash/context7-mcp"
          ];
          required = false;
          startup_timeout_sec = 12;
          tool_timeout_sec = 60;
          env_vars = [ "CONTEXT7_API_KEY" ];
        };
        playwright = {
          command = "npx";
          args = [
            "-y"
            "@playwright/mcp"
          ];
          required = false;
          startup_timeout_sec = 15;
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
  home.file.".codex/config.toml.source".source =
    tomlFormat.generate "codex-config.toml" codexConfigAttrs;

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
