# Phase B qualification tooling

This package belongs to Home's deployment/recovery plane. It does not activate,
start, drain, migrate, graduate, or cut over canonical controllers.

## Fixed production boundaries

The three entry points accept no arguments and do not consult operator `PATH` or
environment overrides:

- `phase-b-execute` reads `/etc/phase-b/trust.json`; `/var/lib/phase-b/inputs`
  contains authorization material only (baseline and grants), never custody or
  F0 evidence. It writes journals/receipts only below `/var/lib/phase-b`.
- `phase-b-collect` runs on an off-host receiver and uses only
  `/var/lib/phase-b-collector/{inbox,artifacts,state}`.
- `phase-b-verify` opens the fixed artifact/journal roots and performs an online,
  one-time compare-and-set through the anchor-bound receiver client.

Test-only Python call points accept disposable roots and accelerated clocks. No
production CLI flag or environment variable exposes those injections.

## Trust model

The separately reviewed NixOS module installs a root-owned `0700` anchor
directory and `0400` canonical JSON anchor. The anchor binds exact signer roles,
namespaces, schemas, source identities, six registry paths, runbook/effect
identities, and immutable executable closure/path/content digests. Production
loading rejects fixture HMAC algorithms. The trusted computing base explicitly
includes source-root/Nix administration and the reviewed booted closure; the
Product Owner remains the sole human authority.

The source executor is root only for orchestration. The bound privilege dropper
runs only the Hermes mutation adapter as the exact anchored source UID/GID.
After B2, the executor journals a cryptographically random capture challenge.
The exact `source-sensor request` and `custody-reader request` clients talk only
to compiled fixed AF_UNIX endpoints. Separate socket-activated services hold the
private signing authority: the source sensor runs as the source UID with
read-only registries and no network/write access, while the custody reader gets
only its root-declarative provider IP allowlist. Neither socket has `WantedBy`
or activates on a generation switch. The fixed `process-inspector capture-f0`
command remains a local read-only inspector. No operator path, command, source
list, namespace, environment value, or signed field selects a capture endpoint.
Each sensor returns a role-signed self-measured envelope bound to the attempt,
baseline, challenge, journal head, and boot identity. The second custody-reader
`NO_OP` capture is also the final F0 custody evidence; the general source sensor
cannot substitute custody bytes. The executor rejects prospective or stale
historical windows, verifies wall/monotonic reception freshness and a common
fully observed stable window of at most five seconds, then atomically fsyncs the
envelope as a root-owned `0400` artifact before its reference enters the journal.
Sensor signers must sign only their own measurement output and must not expose a
generic caller-payload signing API.

The exact `artifact-reader` binding is verified with the other production
closures. Live capture intentionally consumes bounded canonical signed stdout
rather than accepting an artifact path: `_run` caps the response at the strict
JSON bound, rejects noncanonical or non-object output, and capture schemas have
no path field. Routing the same bytes through an artifact-reader pathname would
add a mutable path/TOCTOU surface without adding a signature or identity check.

Test-only `F0EvidenceSource` injection constructs captures only when called;
production always constructs the fixed anchor-bound implementation. Preloaded
`custody-read-*.json` and `f0-evidence.json` files are ignored.

## Deliberate non-activation

`services.phaseBQualification.enable` and `services.phaseBOffhostCollector.enable`
default to false and are mutually exclusive on one host. Even when declared,
their unit templates have no `wantedBy`/`requiredBy`; switching a generation
cannot execute Phase B. Disabling both removes `/etc/phase-b`. The source role
never declares a collector service; the separately enabled off-host role does.

The external self-measuring source-sensor/custody-reader implementations and
their private signing credentials are intentionally unconfigured; this package
does not fake that attestation. The code-only package remains `PARTIAL` until
those implementations plus production signer/receiver bindings, continuous
source transports, a live-compatible Hermes mutation adapter, fresh B0
identities, and the automatic-upgrade/reboot observation conflict are separately
reviewed and configured. Phase B admission and live observation remain
blocked/not started.
