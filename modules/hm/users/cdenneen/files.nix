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
      model = "gpt-5.3-codex";
      model_reasoning_effort = "xhigh";
      model_reasoning_summary = "auto";
      personality = "none";
      file_opener = "none";
      show_raw_agent_reasoning = true;
      web_search = "live";
      features = {
        multi_agent = true;
      };
      mcp_servers = {
        github = {
          command = "github-mcp-server";
          args = [ "stdio" ];
          env = {
            GITHUB_TOOLSETS = "context,actions,code_security,dependabot,discussions,gists,git,issues,labels,notifications,orgs,projects,pull_requests,repos,secret_protection,security_advisories,stargazers,users";
          };
          env_vars = [ "GITHUB_TOKEN" ];
        };
        gitlab = {
          command = "npx";
          args = [
            "-y"
            "@zereight/mcp-gitlab"
          ];
          env = {
            GITLAB_API_URL = "https://git.ap.org/api/v4";
            GITLAB_READ_ONLY_MODE = "true";
          };
          env_vars = [ "GITLAB_TOKEN" ];
        };
        kubernetes = {
          command = "npx";
          args = [
            "-y"
            "@strowk/mcp-k8s"
          ];
        };
        aws = {
          command = "npx";
          args = [
            "-y"
            "aws-mcp-readonly-lite"
          ];
        };
        terraform = {
          command = "podman";
          args = [
            "run"
            "-i"
            "--rm"
            "hashicorp/terraform-mcp-server:0.4.0"
          ];
        };
        duckduckgo = {
          command = "npx";
          args = [
            "-y"
            "ddg-mcp-search"
          ];
        };
        context7 = {
          command = "npx";
          args = [
            "-y"
            "@upstash/context7-mcp"
          ];
          env_vars = [ "CONTEXT7_API_KEY" ];
        };
        playwright = {
          command = "npx";
          args = [
            "-y"
            "@playwright/mcp"
          ];
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

  home.file.".codex/AGENTS.md".source = ./ai/AGENTS.md;
  home.file.".codex/notify.py" = {
    source = ./ai/notify.py;
    executable = true;
  };
  home.file.".codex/config.toml".source = tomlFormat.generate "codex-config.toml" codexConfigAttrs;

  home.activation.awsConfigWrite = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
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

  home.activation.cleanupTelegramBridgeEnv = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    set -euo pipefail

    env_file="$HOME/.config/opencode-telegram-bridge/env"
    if [ -f "$env_file" ]; then
      $DRY_RUN_CMD ${pkgs.coreutils}/bin/rm -f "$env_file"
    fi
  '';

  home.file.".kube/switch-config.yaml".source = ./switch-config.yaml;
}
