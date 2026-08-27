{ config, lib, ... }:
let
  cfg = config.profiles.hermesSingleWriterRegistry;
in
{
  # BOOT-002/024: single-writer safety must be structural, not remembered
  # operationally. The historical failure this closes: Nyx ran two
  # processes (one Nix-managed, one an unmanaged manually-placed unit)
  # both targeting the same HERMES_HOME simultaneously (a real,
  # confirmed, live crash loop - fixed at the time by retiring the
  # unmanaged unit, but nothing then prevented recurrence).
  #
  # Every gateway/supervisor module that owns a writable Hermes home
  # appends one entry here (see hermes-supervisor/default.nix,
  # hermes-axis-control-gateway.nix). This module's own assertion then
  # rejects, at build/eval time, any host configuration that declares two
  # writable owners for the same effective HERMES_HOME - a config
  # collision fails the build, not the runtime.
  #
  # This module only catches Nix-DECLARED collisions. The runtime half
  # (an unmanaged/legacy process, or two declared services racing at
  # startup) is closed independently by each gateway wrapping its
  # ExecStart in a non-blocking flock on a lockfile inside its own
  # HERMES_HOME (see hermes-single-writer-lock.nix) - a kernel-level file
  # lock is enforced regardless of whether the second writer is
  # Nix-declared at all.
  options.profiles.hermesSingleWriterRegistry.entries = lib.mkOption {
    type = lib.types.listOf (
      lib.types.submodule {
        options = {
          name = lib.mkOption {
            type = lib.types.str;
            description = "systemd unit/service name declaring this HERMES_HOME.";
          };
          hermesHome = lib.mkOption {
            type = lib.types.str;
            description = "Effective HERMES_HOME path this entry writes to (evaluated, not a runtime %h specifier).";
          };
          writable = lib.mkOption {
            type = lib.types.bool;
            default = true;
            description = "Whether this entry actually mutates/dispatches against its HERMES_HOME (vs. a genuinely read-only inspection tool, which may legitimately share a home with its writable counterpart).";
          };
        };
      }
    );
    default = [ ];
    internal = true;
  };

  config.assertions =
    let
      writable = builtins.filter (e: e.writable) cfg.entries;
      byHome = lib.groupBy (e: e.hermesHome) writable;
      duplicates = lib.filterAttrs (home: es: builtins.length es > 1) byHome;
      report = lib.concatStringsSep "; " (
        lib.mapAttrsToList (
          home: es: "${home} claimed by [${lib.concatStringsSep ", " (map (e: e.name) es)}]"
        ) duplicates
      );
    in
    [
      {
        assertion = duplicates == { };
        message = "hermes-single-writer-registry (BOOT-002/024): duplicate writable HERMES_HOME ownership detected - ${report}. Two services on this host are declared to both write to the same Hermes home - exactly the historical Nyx duplicate-gateway collision. Give each a distinct HERMES_HOME/profile, or mark one entry writable=false if it is genuinely read-only.";
      }
    ];
}
