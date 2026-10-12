"""
SECURITY FIX: Close the delegated-child Kanban mutation bypass via
subprocess env stripping (found 2026-10-08, incident t_cad1c22f).

Root cause
----------
``_assert_not_delegated_child_mutation()`` in kanban_db.py relies on two
cooperative signals to identify delegated children:

  1. The ``_DELEGATED_CHILD_CONTEXT`` ContextVar — present only in the
     parent process while running the child via ``delegated_child_context()``.
  2. The ``HERMES_DELEGATED_CHILD_CONTEXT=1`` env var — written by
     ``scrub_kanban_env()`` into every subprocess the child spawns.

A child can bypass BOTH by calling ``os.environ.pop('HERMES_DELEGATED_CHILD_CONTEXT')``
before spawning ``$HERMES_PYTHON -c "...handle_function_call('kanban_complete',...)"``.
In that fresh subprocess there is no ContextVar (new process) and no env var
(explicitly removed), so the check returns ``delegated=False`` and the
mutation goes through.

The exploit also manually sets ``HERMES_KANBAN_TASK`` to the target task id
but cannot recover ``HERMES_KANBAN_CLAIM_LOCK`` (it was scrubbed by
``scrub_kanban_env()`` and is never surfaced to the child). A legitimate
dispatcher-spawned worker has BOTH ``HERMES_KANBAN_TASK`` AND
``HERMES_KANBAN_CLAIM_LOCK`` set. Any process with TASK set but CLAIM_LOCK
absent is therefore either:
  - a subprocess that has had its worker identity scrubbed (delegated child), or
  - a process that manually set HERMES_KANBAN_TASK but not the claim lock.
Neither of those should be allowed to mutate Kanban state.

Fix
---
Wrap ``_assert_not_delegated_child_mutation()`` to add a third check:
if ``HERMES_KANBAN_TASK`` is set but ``HERMES_KANBAN_CLAIM_LOCK`` is absent,
raise ``PermissionError`` with the same message as the existing checks.

This is defence-in-depth alongside the existing ContextVar/env-var checks.
It explicitly closes the env-stripping bypass without breaking:
  - Legitimate dispatcher workers (both vars present).
  - Human CLI / orchestrators (neither var present).
  - In-process delegated children (caught earlier by ContextVar check).
  - Subprocesses with HERMES_DELEGATED_CHILD_CONTEXT set (caught by env check).

kanban_db_connect.write_txn calls ``_kb._assert_not_delegated_child_mutation()``
via live module-attribute lookup (``_kb`` is the kanban_db module object), so
patching the attribute on the module is sufficient — write_txn sees the new
function on every call without any additional plumbing.

The fix is fail-open at the import level: if kanban_db is not yet
imported or has a different shape, the original function is used unchanged.

Ref: t_71552fbf (2026-10-08).
"""
try:
    import os

    import hermes_cli.kanban_db as _kanban_db

    _orig_assert_not_delegated = _kanban_db._assert_not_delegated_child_mutation

    def _patched_assert_not_delegated_child_mutation() -> None:
        """Augmented guard: original checks + task-present/lock-absent heuristic.

        Closes the env-stripping bypass: a process that manually sets
        HERMES_KANBAN_TASK but lacks HERMES_KANBAN_CLAIM_LOCK (which is only
        present in processes the dispatcher launched directly) cannot mutate
        Kanban state even if HERMES_DELEGATED_CHILD_CONTEXT was removed.
        """
        # Run the original checks first (ContextVar + HERMES_DELEGATED_CHILD_CONTEXT env).
        _orig_assert_not_delegated()

        # Secondary check: if HERMES_KANBAN_TASK is set but HERMES_KANBAN_CLAIM_LOCK is
        # absent, this process is either a scrubbed subprocess of a worker (delegated
        # child path, env-stripped) or a process that manually injected the task id
        # without a valid claim lock. Refuse the mutation either way.
        if os.environ.get("HERMES_KANBAN_TASK") and not os.environ.get("HERMES_KANBAN_CLAIM_LOCK"):
            raise PermissionError(
                "delegate_task child contexts cannot mutate Kanban tasks or boards "
                "(HERMES_KANBAN_TASK is set but HERMES_KANBAN_CLAIM_LOCK is absent — "
                "this process does not hold a valid dispatcher claim)"
            )

    # Replace the module-level function so write_txn (which calls
    # _kb._assert_not_delegated_child_mutation() via live attribute lookup)
    # and any direct callers in kanban_db pick up the new check.
    _kanban_db._assert_not_delegated_child_mutation = _patched_assert_not_delegated_child_mutation

except Exception:
    # Never let this patch break Hermes startup.
    pass
