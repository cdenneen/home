{ lib, pkgs, ... }:

{
  # User-scoped config files for cdenneen.
  # Keep this limited to small, self-contained files.

  home.activation.ensureAwsConfigDir = lib.hm.dag.entryBefore [ "writeBoundary" ] ''
    mkdir -p "$HOME/.aws"
  '';

  home.activation.awsConfigEc2Patch = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    if [ -f "$HOME/.aws/config" ]; then
      if [ -r /sys/devices/virtual/dmi/id/sys_vendor ] && ${pkgs.gnugrep}/bin/grep -qi "amazon" /sys/devices/virtual/dmi/id/sys_vendor; then
        ${pkgs.gnused}/bin/sed -i 's/source_profile=sso-apss/source_profile=ec2-local/g' "$HOME/.aws/config" || true

        if ! ${pkgs.gnugrep}/bin/grep -q "^\[profile ec2-local\]" "$HOME/.aws/config"; then
          {
            echo ""
            echo "[profile ec2-local]"
            echo "credential_source = Ec2InstanceMetadata"
          } >> "$HOME/.aws/config"
        fi
      fi
    fi
  '';

  home.file.".aws/config".source = ./files/aws-config;
  home.file.".kube/switch-config.yaml".source = ./switch-config.yaml;
}
