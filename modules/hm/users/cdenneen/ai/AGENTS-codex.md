# Codex Agent Guide

<!-- Shared contract — read this first -->

@include ~/.ai/AGENTS.md

---

## Codex-Specific

### RTK Workflow

- RTK guidance lives in `~/.codex/RTK.md`.
- Prefer `rtk <command>` for tests, builds, installs, and verbose git/log commands when available.
- Use raw commands for tiny output or interactive flows where RTK adds friction.

### Sandbox mode

- Default: `workspace-write` — can read/write files and run commands in the workspace.
- Production applies, secret rotation, and infra deploys require explicit approval regardless of mode.

### Subagent routing

- Kubernetes cluster/manifests → `kubernetes-expert`
- Terraform/OpenTofu/Terragrunt → `terraform-expert`
- GitLab CI pipeline/job debugging → `gitlab-ci-expert`
- AWS IAM/OIDC/STS → `aws-expert`
- Nix/NixOS/Home Manager/flake → `nix-expert`
- FluxCD/Flux Operator/GitOps → `flux-expert`
- Mixed tasks: split by domain, synthesize one final answer.

### Session update (Codex equivalent of cos-update)

Codex does not have the `cos-update` Hermes skill. Use the MCP equivalent:

1. `store_context(content=<update>, type="session", agent_id="codex@<host>", project=<slug>)`
2. `write_handoff` at gates/blockers/completion.
3. At session end: print the HANDOFF resume prompt so the human or CoS can resume.
