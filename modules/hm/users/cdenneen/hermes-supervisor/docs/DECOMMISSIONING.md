# Decommissioning Procedure

1. Set mode to draining and stop new claims.
2. Complete or hand off every assignment and lease.
3. Verify no open supervisor MRs, pending deliveries, or recovery actions.
4. After active executions finish, run
   `axis-development-supervisor-cronctl remove --hermes "$(command -v hermes)"`.
5. Record final repository/main/pipeline/worktree/branch state.
6. Archive required operational receipts and GitLab evidence links.
7. Revoke supervisor-specific credentials and remove temporary worktrees.
8. Disable the supervisor Home Manager module and activate it.
9. Verify the general Hermes gateway remains healthy for unrelated uses.
10. Mark the bootstrap capability decommissioned; never migrate its authority
    into AXIS automatically.
