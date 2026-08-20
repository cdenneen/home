# Dormant Ghost producer composition

This candidate composes exactly two canonical private producers:

- `ghostspace-com/axis-control@721d7f93362feb8ad172dd9b3f057cdc1e0e75e4`
- `ghostspace-com/alpha0@c000ed805b9231e39b8240469ca398a19e006aed`

The embedded AXIS supervisor is retained at `modules/hm/users/cdenneen/hermes-supervisor/` as mixed generic-gateway source with its AXIS supervisor gate forced false; the embedded Alpha0 module and routing shim are archived at `docs/recovery/legacy-alpha0-gateway/`. Neither archive is imported. The remaining legacy axis-control gateway option is explicitly false and renders no unit. The canonical axis-control module renders only its report-only watchdog definition and profile wrapper; it installs no scheduler or gateway. Alpha0 Core and gateway gates are false and render neither service nor scheduler inventory. The primary generic Hermes gateway is unchanged.

No Home Manager switch, service start, scheduler installation, gateway restart, GitLab write, dispatch, repair, merge, or autonomy graduation is authorized by this PR. On a separately authorized future Home activation, `hermesLegacyAxisCronDecommission` scans unprofiled and profile-scoped Hermes state and removes only legacy supervisor/report/watchdog jobs whose IDs, names, commands, and cadences exactly match their legacy control records; ambiguity or drift fails closed and generic Hermes jobs are preserved. The canonical scheduler remains disabled. Rollback is a Git revert of this composition before activation.

## CI source-access contract

Hosted CI requires repository secret `GHOSTSPACE_PRODUCER_READ_TOKEN`, a fine-grained token limited to read-only Contents/Metadata for only `ghostspace-com/axis-control` and `ghostspace-com/alpha0`, plus `GITLAB_AXIS_READ_USERNAME` and `GITLAB_AXIS_READ_TOKEN` from one project deploy token limited to `read_repository` for `ghostspace/axis`. They are CI source credentials only, distinct from all future runtime credentials. Workflows pass the GitHub credential only through the Nix process `access-tokens` setting and expose the GitLab credential only to a step-scoped, secret-free `GIT_ASKPASS` helper; neither credential is interpolated into Nix expressions, derivations, store paths, outputs, or logs. `GH_PAT` is not used. Missing/denied credentials are `PRIVATE_DEPENDENCY_ACCESS`, never grounds to weaken evaluation.

CI failures are classified as:

- `PRODUCER_REGRESSION`: pinned producer's own exact-head check fails after successful fetch.
- `CONSUMER_REGRESSION`: this composition/evaluation fails after all inputs fetch and producer checks pass.
- `PRIVATE_DEPENDENCY_ACCESS`: authentication/authorization or private input fetch fails.
- `PRE_EXISTING_HOME_FAILURE`: reproduced unchanged on the exact base; currently the Darwin `minimal-vm.nix` `boot` option failure.

Server-side `main` protection requires a PR, one approval, stale-review dismissal, last-push approval, admin enforcement, linear history, conversation resolution, and disallows force-push/deletion. Strict, up-to-date `nixos-eval` and `darwin-eval` status contexts are configured as required checks. Existing change/revert history is retained. `scripts/install-git-guards.sh` installs the additional local pre-push guard; an emergency direct push requires an explicit audited `ALLOW_DIRECT_MAIN_PUSH=<incident>` override and remains subject to server protection.
