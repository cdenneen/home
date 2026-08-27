{ pkgs, agentPkgs }:
let
  workloadMetadataPy = builtins.readFile ./sitecustomize.py;
in
{
  # Every gateway that needs this patch already carries its own
  # sitecustomize.py (hermesGatewaySitecustomize, for the
  # should_bypass_active_session workaround) wired via PYTHONPATH.
  # Python's site.py imports exactly one sitecustomize module regardless of
  # how many directories are colon-joined onto PYTHONPATH, so this patch
  # must be appended into that same file's text, not shipped as a second
  # PYTHONPATH entry (which would silently shadow one patch or the other).
  mkCombinedSitecustomize =
    existingPatchPy:
    pkgs.writeTextDir "sitecustomize.py" ''
      ${existingPatchPy}
      ${workloadMetadataPy}
    '';

  # Functional self-test, run as an additional ExecStartPre alongside each
  # gateway's existing bypass-check script (ExecStartPre accepts a list;
  # every entry must succeed). Fails closed if Hermes 0.20.0's
  # aux_accounting.set_accounting_context / chat_completions.
  # _add_prompt_cache_key signatures change.
  selftestCheck = pkgs.writeShellScript "hermes-workload-metadata-selftest" ''
    set -euo pipefail
    exec ${agentPkgs.hermes.hermesVenv}/bin/python3 ${./selftest.py}
  '';
}
