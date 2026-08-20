# Alpha0 producer migration

Ghost imports `alpha0.homeModules.default` from canonical private producer
`ghostspace-com/alpha0@a1715b45d8d8db2470868819fb1973a1998b6c5b`. The producer's independent
`services.alpha0.enableCore` and `services.alpha0.enableGateway` gates are both
false, so this change installs no Alpha0 service, gateway, scheduler, or runtime
state. The default data path remains `$XDG_DATA_HOME/alpha0` (currently
`/home/cdenneen/.local/share/alpha0`), never `/.local/share/alpha0`.

The previous embedded Home Manager module and routing shim are retained only as
non-imported recovery evidence under `docs/recovery/legacy-alpha0-gateway/`.
They are not deployment authority and must not be re-imported beside the
canonical producer. Alpha0 may supervise axis-control but does not own or
replicate AXIS roadmap, branch, merge-request, merge, or other SDLC authority.

Graduation requires a separate exact-head change, independent review, current
main CI, recovered owner-only secret references, signed deployment evidence,
and explicit authorization for each Core, gateway, and scheduler transition.
Rollback this dormant migration by reverting its commit; that restores the old
source location and input graph without activating either implementation.
