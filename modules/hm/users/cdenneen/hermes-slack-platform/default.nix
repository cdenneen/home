{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.profiles.hermesSlackPlatformOverride;
  targetPaths = map (target: target.homeRelativePath) (builtins.attrValues cfg.targets);
  invalidTargets = lib.filterAttrs (
    _name: target:
    let
      segments = lib.splitString "/" target.homeRelativePath;
    in
    target.homeRelativePath == ""
    || lib.hasPrefix "/" target.homeRelativePath
    || builtins.any (
      segment:
      builtins.elem segment [
        ""
        "."
        ".."
      ]
    ) segments
  ) cfg.targets;
in
{
  options.profiles.hermesSlackPlatformOverride.targets = lib.mkOption {
    type = lib.types.attrsOf (
      lib.types.submodule {
        options.homeRelativePath = lib.mkOption {
          type = lib.types.str;
          description = "Hermes home, relative to the Home Manager user's home directory.";
        };
      }
    );
    default = { };
    description = ''
      Hermes homes that receive the shared Slack platform override. Gateway
      modules register their own targets; host modules may add targets owned
      by external gateway modules.
    '';
  };

  config = lib.mkIf (cfg.targets != { }) {
    assertions = [
      {
        assertion = invalidTargets == { };
        message = "Hermes Slack platform target paths must be normalized, non-empty, and home-relative.";
      }
      {
        assertion = builtins.length targetPaths == builtins.length (lib.unique targetPaths);
        message = "Hermes Slack platform target paths must be unique.";
      }
    ];

    home.file = lib.mapAttrs' (
      _name: target:
      lib.nameValuePair "${target.homeRelativePath}/plugins/platforms/slack" {
        source = ./plugin;
        force = true;
      }
    ) cfg.targets;

    home.activation.hermesSlackPlatformConfig = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
      configure_plugin() {
        config_file="$1"
        if [ ! -f "$config_file" ] || [ -n "''${DRY_RUN_CMD:-}" ]; then
          return
        fi
        config_tmp="$(${pkgs.coreutils}/bin/mktemp --tmpdir hermes-slack-plugin-config.XXXXXX)"
        ${pkgs.yq-go}/bin/yq '
          .plugins.enabled = (((.plugins.enabled // []) | map(select(. != "slack-platform"))) + ["platforms/slack"] | unique)
          | .plugins.disabled = ((.plugins.disabled // []) | map(select(. != "platforms/slack" and . != "slack-platform")))
        ' "$config_file" > "$config_tmp"
        if ! ${pkgs.diffutils}/bin/cmp -s "$config_tmp" "$config_file" \
          || [ "$(${pkgs.coreutils}/bin/stat -c %a "$config_file")" != 600 ]; then
          ${pkgs.coreutils}/bin/install -m 600 -T "$config_tmp" "$config_file"
        fi
        ${pkgs.coreutils}/bin/rm -f "$config_tmp"
      }

      ${lib.concatMapStringsSep "\n" (
        target:
        "configure_plugin ${lib.escapeShellArg "${config.home.homeDirectory}/${target.homeRelativePath}/config.yaml"}"
      ) (builtins.attrValues cfg.targets)}
    '';
  };
}
