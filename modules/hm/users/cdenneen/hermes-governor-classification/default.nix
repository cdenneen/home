{ pkgs, agentPkgs }:
let
  governorClassificationPy = builtins.readFile ./sitecustomize_patch.py;
in
{
  # Raw patch text - combined with any other gateway's existing patch text
  # via mkCombinedSitecustomize (hermes-workload-metadata/default.nix),
  # since Python's site.py imports exactly one sitecustomize module
  # regardless of how many directories are colon-joined onto PYTHONPATH.
  inherit governorClassificationPy;

  # Functional self-test, run as an ExecStartPre before the gateway
  # starts - mirrors hermes-workload-metadata's existing precedent.
  # Fails closed if Hermes 0.20.0 upstream changes
  # agent.error_classifier.classify_api_error's or
  # gateway.run._gateway_provider_error_reply's shape/behavior.
  selftestCheck = pkgs.writeShellScript "hermes-governor-classification-selftest" ''
    set -euo pipefail
    exec ${agentPkgs.hermes.hermesVenv}/bin/python3 ${./selftest.py}
  '';
}
