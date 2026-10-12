"""
Self-test for the hermes-kanban-delegation-fence sitecustomize patch.

Verifies that _assert_not_delegated_child_mutation() blocks mutations from
a process that has HERMES_KANBAN_TASK set but HERMES_KANBAN_CLAIM_LOCK
absent (the env-stripping bypass discovered 2026-10-08, t_71552fbf).

Run as ExecStartPre before the gateway starts; exits 0 on success, 1 on failure.
"""
import os
import sys

def run_selftest():
    # Ensure the patch file is applied before we import kanban_db.
    # The sitecustomize path is injected via PYTHONPATH by the gateway service,
    # so by this point sitecustomize.py has already run. We just verify the result.
    try:
        import hermes_cli.kanban_db as kanban_db
    except ImportError as e:
        print(f"SKIP: cannot import hermes_cli.kanban_db ({e})", file=sys.stderr)
        return 0  # Not a failure; only present in the hermes venv.

    # Save original env state.
    orig_task = os.environ.pop("HERMES_KANBAN_TASK", None)
    orig_lock = os.environ.pop("HERMES_KANBAN_CLAIM_LOCK", None)
    orig_delegated = os.environ.pop("HERMES_DELEGATED_CHILD_CONTEXT", None)

    try:
        # --- Test 1: TASK set, LOCK absent => must raise PermissionError ---
        os.environ["HERMES_KANBAN_TASK"] = "t_test1234"
        os.environ.pop("HERMES_KANBAN_CLAIM_LOCK", None)
        os.environ.pop("HERMES_DELEGATED_CHILD_CONTEXT", None)
        try:
            kanban_db._assert_not_delegated_child_mutation()
            print("FAIL: test 1 — expected PermissionError when TASK set, LOCK absent", file=sys.stderr)
            return 1
        except PermissionError:
            pass  # Correct — patch is active.
        except Exception as e:
            print(f"FAIL: test 1 — unexpected exception: {e}", file=sys.stderr)
            return 1
        print("PASS: test 1 — TASK set, LOCK absent => PermissionError raised")

        # --- Test 2: Both TASK and LOCK set => must NOT raise ---
        os.environ["HERMES_KANBAN_TASK"] = "t_test1234"
        os.environ["HERMES_KANBAN_CLAIM_LOCK"] = "ghost:99999:fake"
        os.environ.pop("HERMES_DELEGATED_CHILD_CONTEXT", None)
        try:
            kanban_db._assert_not_delegated_child_mutation()
        except PermissionError as e:
            print(f"FAIL: test 2 — unexpected PermissionError when TASK+LOCK both set: {e}", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"FAIL: test 2 — unexpected exception: {e}", file=sys.stderr)
            return 1
        print("PASS: test 2 — TASK+LOCK both set => no error (legitimate dispatcher worker)")

        # --- Test 3: Neither TASK nor LOCK set => must NOT raise ---
        os.environ.pop("HERMES_KANBAN_TASK", None)
        os.environ.pop("HERMES_KANBAN_CLAIM_LOCK", None)
        os.environ.pop("HERMES_DELEGATED_CHILD_CONTEXT", None)
        try:
            kanban_db._assert_not_delegated_child_mutation()
        except PermissionError as e:
            print(f"FAIL: test 3 — unexpected PermissionError for orchestrator/CLI: {e}", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"FAIL: test 3 — unexpected exception: {e}", file=sys.stderr)
            return 1
        print("PASS: test 3 — neither TASK nor LOCK set => no error (orchestrator/CLI)")

        # --- Test 4: Original HERMES_DELEGATED_CHILD_CONTEXT check still works ---
        os.environ.pop("HERMES_KANBAN_TASK", None)
        os.environ.pop("HERMES_KANBAN_CLAIM_LOCK", None)
        os.environ["HERMES_DELEGATED_CHILD_CONTEXT"] = "1"
        try:
            kanban_db._assert_not_delegated_child_mutation()
            print("FAIL: test 4 — expected PermissionError when HERMES_DELEGATED_CHILD_CONTEXT=1", file=sys.stderr)
            return 1
        except PermissionError:
            pass  # Correct — original check still works.
        except Exception as e:
            print(f"FAIL: test 4 — unexpected exception: {e}", file=sys.stderr)
            return 1
        print("PASS: test 4 — HERMES_DELEGATED_CHILD_CONTEXT=1 => PermissionError raised")

    finally:
        # Restore original env state.
        for k, v in [
            ("HERMES_KANBAN_TASK", orig_task),
            ("HERMES_KANBAN_CLAIM_LOCK", orig_lock),
            ("HERMES_DELEGATED_CHILD_CONTEXT", orig_delegated),
        ]:
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    print("All tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(run_selftest())
