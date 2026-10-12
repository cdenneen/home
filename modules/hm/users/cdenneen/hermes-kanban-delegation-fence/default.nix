{ pkgs, agentPkgs }:
let
  kanbanDelegationFencePy = builtins.readFile ./sitecustomize_patch.py;
in
{
  # Raw patch text - combined with any other gateway's existing patch text
  # via mkCombinedSitecustomize (hermes-workload-metadata/default.nix),
  # since Python's site.py imports exactly one sitecustomize module
  # regardless of how many directories are colon-joined onto PYTHONPATH.
  inherit kanbanDelegationFencePy;

  # Functional self-test, run as an ExecStartPre before the gateway
  # starts - mirrors hermes-governor-classification's existing precedent.
  # Fails closed if Hermes changes kanban_db._assert_not_delegated_child_mutation's
  # shape/behavior.
  selftestCheck = pkgs.writeShellScript "hermes-kanban-delegation-fence-selftest" ''
    set -euo pipefail
    exec ${agentPkgs.hermes.hermesVenv}/bin/python3 ${./selftest.py}
  '';
}
