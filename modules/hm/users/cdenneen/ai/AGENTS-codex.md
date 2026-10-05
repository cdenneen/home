# Codex Agent Guide

<!-- Shared contract — read this first -->

@include ~/.ai/AGENTS.md

---

## Codex-Specific

### RTK Workflow

- RTK guidance: `~/.codex/RTK.md`. Prefer `rtk <command>` for tests, builds, installs, verbose git/log when available.

### Sandbox mode

- Default: `workspace-write`. Production applies, secret rotation, infra deploys require explicit approval regardless of mode.

### Subagent routing

- Kubernetes → `kubernetes-expert`, Terraform/OpenTofu → `terraform-expert`, GitLab CI → `gitlab-ci-expert`, AWS IAM → `aws-expert`, Nix/NixOS → `nix-expert`, FluxCD → `flux-expert`. Mixed: split by domain, one final answer.

### Session update (Codex equivalent of cos-update)

1. `store_context(content=<update>, type="session", agent_id="codex@<host>", project=<slug>)`
2. `write_handoff` at gates/blockers/completion.
3. At session end: print `HANDOFF: <id> — Resume: tell @nyxops to resume handoff <id>`
