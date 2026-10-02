#!/usr/bin/env bash
# cos-update — send a structured status update to chief-of-staff@ghost
# Usage: cos-update <event> <message>
# Events: started | blocked | review | completed | update
#
# Called automatically by nyx Hermes profiles at session lifecycle points.
# CoS receives the message and updates the ghost Kanban accordingly.
# Never call hermes kanban directly from nyx — always go through CoS.

set -euo pipefail

HERMES_BIN="@hermes_bin@"
PROFILE="ops"  # caller identity; overridden by HERMES_PROFILE env var
EVENT="${1:-update}"
MESSAGE="${2:-}"
SESSION_ID="${HERMES_SESSION_ID:-unknown}"
HOST="nyx"

if [[ -z "$MESSAGE" ]]; then
  echo "usage: cos-update <event> <message>" >&2
  echo "  events: started | blocked | review | completed | update" >&2
  exit 1
fi

# Resolve caller profile from environment if available
if [[ -n "${HERMES_PROFILE:-}" ]]; then
  PROFILE="$HERMES_PROFILE"
fi

STRUCTURED_MSG="[${PROFILE}@${HOST}] [${EVENT}] session=${SESSION_ID}
${MESSAGE}"

# Send DM to chief-of-staff on ghost — CoS updates the Kanban
exec "$HERMES_BIN" peer dm ghost/chief-of-staff "$STRUCTURED_MSG"
