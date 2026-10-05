# Legacy Ghost evidence inventory

Captured 2026-08-20 from live host `ghost`. This inventory records credential-free evidence only. The local quarantine is not a source repository and must never be added as a Home or producer input.

## Quarantine boundary and verification

Local evidence root: `/home/cdenneen/src/workspace/gss-ops/evidence/ghost-legacy-20260820`

| Measure | Result |
|---|---:|
| Manifested copied candidates | 80 |
| axis-control candidates | 71 |
| Alpha0/deployment candidates | 9 |
| Bundle regular files (including control files and three pre-existing private Alpha0 files) | 90 |
| Bundle directories | 50 |
| Manifest entries | 80 |
| Scanner pattern matches | 0 |
| Missing copies / hash mismatches / extra product files | 0 / 0 / 0 |
| Forbidden copied paths / permission violations / staged repositories | 0 / 0 / 0 |

All copied evidence files and control records are mode `0600`; containing directories are mode `0700`. Source-to-copy SHA-256 parity passed for every manifested entry. The complete per-entry path, size, timestamp, source mode/ownership, content hash, role and classification remain in the local `manifest.md`/`manifest.json`. They are intentionally not duplicated wholesale into Git.

The three files below pre-dated the source-copy pass and were excluded from its manifest and copying logic:

- `alpha0-private/alpha0-ghost-online-backup.db`
- `alpha0-private/alpha0-ghost-online-backup.metadata.json`
- `alpha0-private/alpha0-real-data-test.metadata.json`

They are private recovery evidence, not source evidence. No database, database hash, row payload, session, log, WAL/SHM, credential file, environment file, worktree, virtual environment or cache is committed here.

## axis-control evidence groups

The live root `/home/cdenneen/src/workspace/work/axis-control` has no root `.git` metadata. Current bytes can be attested, but revision, branch, remote and independent review provenance are `UNKNOWN`.

### High-value behavior candidates

| Artifact | Captured SHA-256 | Forensic value | Disposition |
|---|---|---|---|
| `scripts/roadmap_execution_lib.py` | `ad547f4f941d221faa87ba50f3cbfb289ba27308f1049edffd084759ed491866` | dependency/frontier, decomposition, CI/review/custody projection | Requirements and tests only; do not import implementation. |
| `scripts/reconcile-roadmap-execution` | `05d70f1d81251c7b7e6ae32bad49546fe145aadc250f3523388a82e4aeea13e8` | live multi-surface reconciler | Reject as runtime authority; remote effects precede durable state. |
| `scripts/axis_kanban_sync.py` | `a2cc2ea3b41b66ff38cd9c4c70aa4d604589f50034b49a0a7fbe0b6f0b008750` | derived task/dependency projection | Future read-only projection concept only. |
| `scripts/completion_triggers.py` | `58dd7233d4a4a137fb89435e1e846570e03e642b0131399f7a73cb604f3436ab` | claim/restart/partial-ACK prior art | Retain tests as historical evidence; reject lossy queue/weak identity. |
| `scripts/reconcile-ci-wait` | `a239f2f4ed55e48eee52771ab13885538192651af0fb6790d64fd78021a588a8` | CI wait and infrastructure hints | Rebuild only with exact-head complete evidence. |
| `scripts/axis_lineage_fence.py` | `141b3ec2f0aa94217878f19e337eb0a9400c446d031736623e2e9b5094aefe1e` | legacy branch/worktree guard | Reject permissive custody; preserve future push-destination intent. |
| `scripts/workspace_hardening_lib.py` | `79029846b3f6269d8bb1f290c2f4a8424319c899c537e4de3287a90fb492a57e` | branch/MR/worktree safety checks | Port only bounded invariants at an authorized mutating stage. |
| `scripts/po_blocker_notifier.py` | `1c6993567f3d045aae6829a219a4a8c98532dc3a89356f60efb1e11ed250f162` | PO packet/reminder UX | Rebuild on canonical PO evidence and durable delivery ACK. |
| `scripts/axis_scheduler_watchdog.py` | `f2d0285354f842cce91734fa75f5bd2ee54402e5bb6f7e1497e9565d7b4e67ad` | scheduler health prior art | Reject mutation/weak identity; canonical watchdog supersedes it. |

Associated focused tests and heartbeat fixtures were retained. Passing legacy tests establish historical intent, not authority; several positively encode behavior canonical contracts reject.

### Other retained source-like evidence

- operational wrappers for review, model invocation, custody verification, prompt rendering and incident recording;
- prompt and skill documents explaining prior workflow intent;
- deployed root/profile manager and heartbeat-precheck copies, including their divergence;
- `AGENTS.md` and `CLAUDE.md` as legacy policy context.

### Explicit exclusions

`.hermes` databases/sessions/logs/environment files (except the five bounded deployed source scripts), `.codex`, `.pi-subagents`, `.venv`, `.pytest_cache`, `result`, nested worktrees, runtime `state`, review/model transcripts, DB/WAL/SHM files and caches were excluded.

## Alpha0 and deployment evidence

The nine copied entries are:

- deployed profile status wrapper and duplicate root status wrapper;
- deployed daily brief wrapper;
- deployed AXIS operations SITREP wrapper;
- deployed profile-aware routing shim from the immutable store;
- legacy Home Manager Alpha0 module;
- legacy Ghost Home composition;
- rendered Alpha0 gateway unit;
- preserved pre-Home-Manager drop-in.

The daily brief wrapper matches canonical behavior. The two status copies duplicate checkout-coupled legacy logic; the canonical installed wrapper is the replacement. The deployed SITREP wrapper differs from canonical and requires a semantic migration check before any future schedule restore. The legacy Home module/shim are non-imported forensic evidence, not deployment authority.

## Scheduler and unit metadata

Sanitized quarantine metadata records 10 systemd unit/timer identities and five enabled Hermes jobs in three registries, without environment, command, prompt, output, destination or secret values. A separate live trigger-path review identified a sixth enabled job, `81776a5f93c5`, in the rootless checkout's fourth scheduler registry; it was not part of that sanitized registry snapshot. At capture, the generic, axis-control and Alpha0 gateway services were active. Legacy AXIS scheduler/watchdog sources and Alpha0 report schedules were enabled. This metadata is topology evidence only; live state can change after capture.

## Security defects recorded without secret inspection

| Path | Owner:group | Mode | Service reference | Future remediation |
|---|---|---:|---|---|
| `/home/cdenneen/src/workspace/work/axis-control/.hermes/.env` | `cdenneen:users` | `0644` | legacy axis-control Hermes runtime | After custody-approved drain: restrict to `0600`, rotate referenced credentials, and replace with reviewed external secret ownership. |
| `/home/cdenneen/src/workspace/work/axis-control/.hermes/profiles/axis-control/.env` | `cdenneen:users` | `0644` | `hermes-axis-control-gateway.service` profile runtime | Same; do not copy into Nix/store, reports or source control. |

Runtime state directories should ultimately be `0700` and secret/state files `0600`. Remediation and rotation were not authorized and were not performed. Historical Alpha0 config copies with mode `0644` require owner-side content classification before retention; their contents were not read.

## Retention rule

Keep the quarantine owner-only and outside all Git worktrees until canonical issues/PRs have extracted justified invariants. Deleting the quarantine, private Alpha0 backup, live legacy state or Ghost-local refs is forbidden until signed cutover acceptance and retention approval are complete.
