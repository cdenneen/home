{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.profiles.hermesProfileModel;

  mkPatch =
    name: prof:
    let
      configPath = "${config.home.homeDirectory}/${prof.configHomeRelativePath}";
      yqFilter = lib.concatStringsSep " | " (
        lib.mapAttrsToList (path: value: ''.${path} = "${value}"'') prof.modelOverrides
      );
    in
    ''
      config_file=${lib.escapeShellArg configPath}
      if [ -f "$config_file" ] && [ -z "''${DRY_RUN_CMD:-}" ]; then
        config_tmp="$(${pkgs.coreutils}/bin/mktemp "$config_file.XXXXXX")"
        if ! ${pkgs.yq-go}/bin/yq '${yqFilter}' "$config_file" > "$config_tmp"; then
          ${pkgs.coreutils}/bin/rm -f "$config_tmp"
          exit 1
        fi
        if ! ${pkgs.diffutils}/bin/cmp -s "$config_tmp" "$config_file" \
          || [ "$(${pkgs.coreutils}/bin/stat -c %a "$config_file")" != 600 ]; then
          ${pkgs.coreutils}/bin/install -m 600 -T "$config_tmp" "$config_file"
        fi
        ${pkgs.coreutils}/bin/rm -f "$config_tmp"
      fi
    '';
in
{
  options.profiles.hermesProfileModel.profiles = lib.mkOption {
    type = lib.types.attrsOf (
      lib.types.submodule {
        options = {
          configHomeRelativePath = lib.mkOption {
            type = lib.types.str;
            description = "Path to this Hermes profile's config.yaml, relative to the Home Manager user's home directory.";
          };
          modelOverrides = lib.mkOption {
            type = lib.types.attrsOf lib.types.str;
            default = { };
            description = ''
              Dotted yq paths (e.g. "model.default", "auxiliary.compression.model")
              pinned idempotently to a value on every home-manager switch. Fields not
              listed here are left untouched - e.g. a profile kept on tier4-frontier
              intentionally omits "model.default" rather than pinning it.
            '';
          };
        };
      }
    );
    default = { };
    description = ''
      Per-profile Hermes model/auxiliary route pins, enforced idempotently via a yq
      merge-patch (same activation pattern as hermes-slack-platform's plugin config
      and the retired hermes-alpha0-gateway config activation) so a profile's declared
      Eros route can't silently drift from what's checked in here.
    '';
  };

  config = lib.mkIf (cfg.profiles != { }) {
    home.activation.hermesProfileModelConfig = lib.hm.dag.entryAfter [ "writeBoundary" ] (
      lib.concatStringsSep "\n" (lib.mapAttrsToList mkPatch cfg.profiles)
    );
  };
}
