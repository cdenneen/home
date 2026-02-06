{ lib, ... }:

{
  # User-scoped config files for cdenneen.
  # Keep this limited to small, self-contained files.

  home.activation.ensureAwsConfigDir = lib.hm.dag.entryBefore [ "writeBoundary" ] ''
    mkdir -p "$HOME/.aws"
  '';

  home.file.".aws/config".source = ./files/aws-config;
  home.file.".kube/switch-config.yaml".source = ./switch-config.yaml;
}
