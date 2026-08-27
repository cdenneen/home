{ pkgs }:
{
  # BOOT-002/024 runtime half: wraps a service's ExecStart in a
  # non-blocking flock on a lockfile inside its own HERMES_HOME. If any
  # other process already holds it - Nix-declared or not, since this is a
  # kernel-level file lock, not a Nix-only mechanism - flock exits 78
  # *without ever running the wrapped command*. 78 matches these units'
  # existing `RestartPreventExitStatus = 78` convention exactly (already
  # used for the analogous should_bypass_active_session bypass-check
  # failure), so a genuine ownership collision fails safely - bounded, no
  # restart-loop - while a transient crash still retries normally via the
  # unit's ordinary Restart=on-failure.
  #
  # `flock <lockfile> -- <argv...>` execs its command directly (systemd's
  # own ExecStart word-splitting, not a shell), so this only works
  # correctly for ExecStart values that are already argv-safe (no shell
  # metacharacters) - true of every gateway ExecStart in this repo today.
  wrapExecStart =
    {
      lockPath,
      execStart,
    }:
    "${pkgs.util-linux}/bin/flock -n -E 78 ${lockPath} -- ${execStart}";
}
