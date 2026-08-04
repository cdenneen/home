# Supervisor Runbook

## Start

Validate control/schema, gateway/Slack, GitLab auth, disk, inventory, leases,
and cron jobs. Begin observing before enabling mutation.

## Cycle

Guard -> collect live sources -> validate inventory -> resolve semantic
authority/decomposition -> rebuild graph -> dispatch -> claim lease -> generate
bounded prompt -> launch fresh Hermes worker -> ingest handoff -> fresh
integration -> verify main -> cleanup -> graph rebuild -> deterministic report.

## Incident

Set `mode=draining` for controlled stop or `kill_switch=true` for immediate
preflight suppression. Preserve GitLab evidence and active assignment state.

## Change management

Modify only the versioned home-flake source. Run unit/fixture tests, Home Manager
build, fault injection, PR review/CI, staged activation, then resume jobs.
