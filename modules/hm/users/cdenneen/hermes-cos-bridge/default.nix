{
  config,
  lib,
  pkgs,
  agentPkgs,
  ...
}:
let
  cfg = config.profiles.hermesCOSBridge;
  hermesPackage = if agentPkgs != null then agentPkgs.hermes else null;
  hermesBin =
    if hermesPackage != null then
      "${hermesPackage}/bin/hermes"
    else
      "/run/current-system/sw/bin/hermes";

  # cos-update: structured status reporter from any nyx worker to chief-of-staff@ghost.
  # Workers call this instead of touching the ghost Kanban directly.
  cosUpdateBin = pkgs.writeShellScriptBin "cos-update" ''
        set -euo pipefail

        EVENT="''${1:-update}"
        MESSAGE="''${2:-}"
        SESSION_ID="''${HERMES_SESSION_ID:-unknown}"
        PROFILE="''${HERMES_PROFILE:-unknown}"
        HOST="nyx"

        if [[ -z "$MESSAGE" ]]; then
          echo "usage: cos-update <event> <message>" >&2
          echo "  events: started | blocked | review | completed | update" >&2
          exit 1
        fi

        STRUCTURED="[''${PROFILE}@''${HOST}] [''${EVENT}] session=''${SESSION_ID}
    ''${MESSAGE}"

        exec "${hermesBin}" peer dm ghost/chief-of-staff "$STRUCTURED"
  '';

  # CoS reporting fragment — appended to each profile's SOUL.md via nyx-home.nix.
  # Exported so nyx-home.nix can include it in the souls attrset.
  cosSoulFragment = ''

    ## Reporting to Chief of Staff (MANDATORY — not optional)

    You have a `cos-update` command. Use it. Do not just describe that you would.

    Call it at these points without waiting to be asked:
    - **Session start**: `cos-update started "Working on: <topic>"`
    - **Blocked**: `cos-update blocked "Blocked: <gate>. Owner: <who>. Parallel work: <what>."`
    - **Review needed**: `cos-update review "<MR/issue link>. Waiting on: <who>."`
    - **Completed / session end**: `cos-update completed "<what done, links, what remains, next action>"`
    - **Significant mid-session state change**: `cos-update update "<one paragraph, factual>"`

    Include GitLab/GitHub links when available. CoS receives this and updates the Ghost Kanban.
    You do not touch the Kanban directly from nyx.
  '';

in
{
  options.profiles.hermesCOSBridge = {
    enable = lib.mkEnableOption "cos-update tool — nyx worker status reporting to chief-of-staff@ghost";
  };

  # Export the soul fragment so nyx-home.nix can include it in each profile's SOUL.md
  options.profiles.hermesCOSBridge.soulFragment = lib.mkOption {
    type = lib.types.str;
    readOnly = true;
    default = cosSoulFragment;
    description = "SOUL.md fragment to append to every nyx worker profile.";
  };

  config = lib.mkIf cfg.enable {
    home.packages = [ cosUpdateBin ];
  };
}
