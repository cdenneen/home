# Alpha0 real-data recovery

Test date: 2026-08-20. Canonical comparison head: `ghostspace-com/alpha0@c000ed805b9231e39b8240469ca398a19e006aed` (PR #2). Result: **compatible for structural and offline reads; no schema migration required**.

## Safety boundary

The live database at `/home/cdenneen/.local/share/alpha0/alpha0.db` was opened read-only and captured with `sqlite3.Connection.backup` into an owner-only reconciliation directory. It was not file-copied, initialized, migrated, checkpointed or queried for row payload output. All compatibility and initialization tests used disposable copies. The preserved backup remained outside Git and was never opened writable.

Offline tests used an empty isolated environment with GitLab, mutation mode and axis supervision disabled, no managed systems, and a network-denial hook. Network attempts were zero. No provider, gateway, scheduler, GitLab write or service action occurred. No audit key or other secret was read or copied. Private database hashes and payloads are intentionally omitted from this report.

## Backup and custody

- Live source was a regular non-symlink file, owner `cdenneen:users`, mode `0600`.
- Reconciliation directories are mode `0700`; backup and machine metadata are mode `0600`.
- Pre/post source metadata showed the same inode and mode; the online backup did not replace or chmod the source.
- Integrity check returned `ok`; foreign-key check returned zero violations.
- The owner-only preserved copy and metadata live under the local evidence root's `alpha0-private/` directory and are explicitly excluded from source manifests and Git.

The online backup is a consistent SQLite snapshot while the live service remains available; source quiescence was not imposed. It is recovery evidence, not deployment state.

## Canonical schema compatibility

| Check | Result |
|---|---|
| Canonical schema version | 5 |
| Recorded application migrations | 3, 4, 5 |
| Tables, actual / expected | 27 / 27 |
| Missing / extra tables | none / none |
| Column, type, nullability, default or primary-key drift | none |
| Foreign-key declarations, actual / expected | 39 / 39 |
| Foreign-key definition drift | none |
| `PRAGMA integrity_check` | `ok` |
| `PRAGMA foreign_key_check` | 0 violations |
| Migration required | no |

`PRAGMA user_version` is zero because Alpha0 uses `schema_migrations`, not that pragma, as application migration authority. Historical v3/v4 rows are expected provenance; a fresh current database records v5 directly.

Canonical `initialize()` was exercised against a disposable copy. It succeeded, changed zero rows, preserved all table counts and retained integrity with zero foreign-key violations. The preserved backup was unchanged.

## Aggregate data compatibility

No row payloads were emitted. Aggregate structural/read results were:

| Surface | Result |
|---|---:|
| Core projects | 10, all readable and active |
| Action proposals | 34: 25 authorized, 9 reconciled |
| Executions | 34: 23 failed, 11 reconciled |
| Audit events | 466 |
| Observations | 139 |
| Evidence records | 161 |
| Claims | 70 |
| Decisions / assumptions | 0 / 0 |

Canonical read-only connection opened the disposable database. Unsigned structural audit verification recomputed all redacted-payload digests, event hashes and previous-hash links across 466 events successfully. HMAC signature verification was intentionally not attempted because it requires the production audit key. A full `Alpha0Core(..., read_only=True)` open with a deliberately wrong offline key failed closed at audit verification, as required.

## Rendering behavior

Canonical code rendered project status, daily action brief and daily operating brief offline without mutation.

- Daily-brief state included all 10 active Core projects.
- Active provider projections: 45 total: 6 attention, 9 cruft, 22 informational and 8 schedule.
- Provider version state: 45 active and 10 superseded.
- Reflection classification was ready/candidate-only with 20 terminal outcomes considered and mutation false.
- Pending R2/R3 approval cards: zero.

`alpha0 status` is an external-authority GitLab view, not a renderer of the Core `projects` table. With GitLab disabled it correctly rendered paused with zero GitLab projects, while the 10 Core projects remained readable through daily-brief JSON state. This is a product-semantic distinction, not data loss.

## Runtime and wrapper reconciliation

The live Core database exists without an installed/running `alpha0-core.service`; only the legacy dedicated Alpha0 Hermes gateway is active. The gateway preserves valuable behavior: dedicated owner home/profile, clean environment, loopback-only API, external supervisor, hardened state and profile-aware clarification/session routing.

The deployed daily-brief adapter matches canonical behavior. The two deployed status wrapper copies duplicate checkout-coupled legacy logic and must be replaced by the pinned canonical installed wrapper. The deployed hourly AXIS SITREP differs from canonical. Its schedule and bounded script-only delivery are useful, but canonical `--apply` managed-state semantics require separate review before restoration. None of these wrappers may grant Alpha0 AXIS roadmap/branch/MR/merge authority.

## Recovery acceptance still required

1. Independently attest availability/ownership of the production audit-key reference without copying its value, then verify signed audit-chain open in an authorized owner-only procedure.
2. Reconstruct disabled Hermes root/profile routes and scheduler declarations from reviewed VCS with fresh runtime state, then prove profile-aware routing preflights. Archive source sessions, Kanban, execution and ticker state without importing them; only a separately qualified owner-selected generic session subset may receive a disposable semantic-restore test.
3. Prove one-owner routing for the dedicated Alpha0 channel and profile-aware session keys; do not fall back to the generic or AXIS gateway.
4. Reconcile the live status and SITREP wrapper semantics against the pinned package; restore schedules only through a separate reviewed, exact-head change.
5. Obtain independently signed, root-owned deployment evidence binding the package, configuration, secret-file references, entrypoints and scheduler identity.
6. Retain three older integrity-clean backups and sidecars as historical evidence until recovery policy classifies them; they predate schema v5 and are not current recovery proof.

## Classification

`ALPHA0_REAL_DATA_RECOVERY = PASS_WITH_LIMITS`: data and schema are structurally compatible and database migration is unnecessary. Signed Core-open, exclusive Hermes route/job reconstruction, external integrations and deployment proof remain incomplete. No Alpha0 Hermes runtime state is currently a mandatory migration input. Activation remains forbidden.
