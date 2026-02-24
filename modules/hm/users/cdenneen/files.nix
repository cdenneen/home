{ lib, pkgs, ... }:

{
  # User-scoped config files for cdenneen.
  # Keep this limited to small, self-contained files.

  home.activation.ensureAwsConfigDir = lib.hm.dag.entryBefore [ "writeBoundary" ] ''
    $DRY_RUN_CMD mkdir -p "$HOME/.aws"
  '';

  # Keep the repo-managed aws config as a store symlink, then copy it into place
  # so it can be patched on EC2 (store paths are read-only).
  home.file.".aws/config.source".source = ./files/aws-config;

  home.file.".config/opencode/AGENTS.md".source = ./opencode/AGENTS.md;
  home.file.".config/opencode/docs/agent-commands.md".source = ./opencode/docs/agent-commands.md;
  home.file.".config/opencode/docs/agent-secrets.md".source = ./opencode/docs/agent-secrets.md;

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
