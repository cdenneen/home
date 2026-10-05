---
name: gitlab-pipelines
description: Debug GitLab CI pipelines, jobs, artifacts, and child/bridge pipelines, and drive GitLab-based Terraform/OpenTofu/Terragrunt deployments. Use for any task touching a GitLab pipeline, job log, artifact, manual job, or AWS OIDC/assume-role failure in CI. Defines the bounded-polling contract, manual-job authorization policy, and the required Run Summary output block.
---

# GitLab IaC Pipelines (glab + Terraform/OpenTofu/Terragrunt + AWS OIDC)

This section applies only when the task concerns a GitLab pipeline, job, artifact, child pipeline, or GitLab-driven infrastructure deployment. It does not impose output or stopping requirements on unrelated work.

### Quick Reference

- Never claim ongoing monitoring; use bounded polling and finish in-run.
- Always check pipeline + child pipelines + artifacts; report job URLs.
- Manual job blocks unless the current request explicitly names and authorizes that job; otherwise stop and request human action with the exact `glab` command.
- End every GitLab pipeline/deployment response with the “Run Summary” block.

### 0) Execution Contract (NO fake monitoring)

- The agent must NOT say: “I will monitor”, “I’ll keep an eye on it”, “I’ll report back later”.
- The agent operates in single-run mode: it must do all polling/retries NOW, inside this run.
- If a task requires waiting on external state (pipeline/jobs), the agent must implement a bounded polling loop (see §3).
- If the pipeline is blocked on a manual job not explicitly named and authorized in the current request, stop polling and request the required human action with exact `glab` commands/URL. If it is pre-authorized, play it and continue polling.

### 1) Definition of Done (DoD)

A deployment task is only “done” when:

1. The target pipeline reaches a terminal state (success/failed/canceled), AND
2. All relevant child/bridge pipelines are also terminal, AND
3. If the workflow depends on artifacts, the agent has checked whether artifacts exist and were produced by the expected job(s), AND
4. The agent prints a final status summary including:
   - pipeline ID + URL
   - failing job(s) with stage
   - child pipeline status (if any)
   - next action (none / manual play / patch + rerun)

### 2) Tooling rules (glab is source of truth)

Use `glab` CLI to query:

- pipeline status
- jobs list (including manual jobs)
- job logs
- artifacts
- downstream/child pipelines (bridges)

Never assume a pipeline is “running normally” — always check for manual jobs or stalled stages.

Auth preference:

- Prefer `glab` host auth from `~/.config/glab-cli/config.yml` (managed as secret).
- Do not require `GITLAB_TOKEN` for `glab` workflows.

### 3) Polling / Monitoring MUST be executed (evidence required)

If the pipeline is not in a terminal state (success/failed/canceled) AND not blocked on a manual job,
the agent MUST run an actual polling loop inside this run.

Compliance requirements:

- The agent MUST perform at least 3 polling iterations before returning (unless it reaches terminal state earlier).
- The agent MUST print a "Poll Log" table with one row per poll iteration:
  - poll number
  - timestamp (UTC or local)
  - parent pipeline status
  - child pipeline statuses (if any)
  - any newly failed jobs
  - any manual jobs detected

If the agent cannot run a loop in the current environment, it MUST say:
"Loop execution unavailable in this runtime" and then output a standalone shell script the user can run
to monitor the pipeline, including child pipelines and manual-job detection.

The agent must never output "Next action: wait" without having executed the minimum poll iterations.

## Non-terminal states are not an acceptable stopping point

The agent must NOT end a run with pipeline status in {running, pending} unless:

- the pipeline is blocked by a manual job not explicitly authorized in the current request (then exit as "Requires human action"), OR
- the polling loop reached TIMEOUT and a Poll Log is printed.

### Example polling monitor (standalone script)

```bash
#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-:id}"      # glab supports :id in many endpoints when in repo
PIPELINE_ID="${1:?usage: $0 <pipeline_id>}"
SLEEP="${SLEEP:-25}"
MAX_POLLS="${MAX_POLLS:-30}"
AUTHORIZED_MANUAL_JOBS="${AUTHORIZED_MANUAL_JOBS:-}" # comma-separated exact job names

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

get_pipeline_status() {
  glab api "projects/$PROJECT_ID/pipelines/$PIPELINE_ID" | jq -r '.status'
}

get_child_pipelines() {
  # bridges endpoint; if your GitLab differs, adjust here
  glab api "projects/$PROJECT_ID/pipelines/$PIPELINE_ID/bridges" --paginate 2>/dev/null \
    | jq -r '.[] | "\(.downstream_pipeline.id)\t\(.downstream_pipeline.status)"' || true
}

get_jobs() {
  glab api "projects/$PROJECT_ID/pipelines/$PIPELINE_ID/jobs" --paginate \
    | jq -r '.[] | "\(.id)\t\(.name)\t\(.stage)\t\(.status)\t\(.web_url)"'
}

manual_jobs() {
  get_jobs | awk -F'\t' '$4=="manual"{print}'
}

is_authorized_manual_job() {
  [[ ",${AUTHORIZED_MANUAL_JOBS}," == *",$1,"* ]]
}

play_authorized_manual_jobs() {
  played_authorized_manual_job=false
  while IFS=$'\t' read -r id name stage status url; do
    [[ -n "$id" ]] || continue
    if is_authorized_manual_job "$name"; then
      echo "PLAYING AUTHORIZED MANUAL JOB: $name ($url)"
      glab api --method POST "projects/$PROJECT_ID/jobs/$id/play" >/dev/null
      played_authorized_manual_job=true
    fi
  done < <(manual_jobs)
}

detect_manual_blockers() {
  while IFS=$'\t' read -r id name stage status url; do
    [[ -n "$id" ]] || continue
    if ! is_authorized_manual_job "$name"; then
      printf '%s\t%s\t%s\t%s\t%s\n' "$id" "$name" "$stage" "$status" "$url"
    fi
  done < <(manual_jobs)
}

detect_failed_jobs() {
  get_jobs | awk -F'\t' '$4=="failed"{print}'
}

echo -e "poll\ttime\tparent_status\tchild_pipelines\tmanual_jobs\tfailed_jobs"
played_authorized_manual_job=false

for ((i=1;i<=MAX_POLLS;i++)); do
  play_authorized_manual_jobs
  parent="$(get_pipeline_status || echo unknown)"
  child="$(get_child_pipelines | paste -sd',' -)"
  manual_cnt="$(detect_manual_blockers | wc -l | tr -d ' ')"
  failed_cnt="$(detect_failed_jobs | wc -l | tr -d ' ')"

  echo -e "${i}\t$(ts)\t${parent}\t${child:-none}\t${manual_cnt}\t${failed_cnt}"

  if [[ "$failed_cnt" -gt 0 ]]; then
    echo "FAILED JOBS:"
    detect_failed_jobs
    exit 2
  fi

  if [[ "$manual_cnt" -gt 0 ]]; then
    echo "MANUAL JOBS BLOCKING:"
    detect_manual_blockers
    exit 3
  fi

  if [[ "$played_authorized_manual_job" == true ]]; then
    sleep "$SLEEP"
    continue
  fi

  if [[ "$parent" =~ ^(success|failed|canceled|skipped)$ ]]; then
    exit 0
  fi

  sleep "$SLEEP"
done

echo "TIMEOUT after $MAX_POLLS polls"
exit 4
```

### 4) Manual jobs (pipelines that are “running” but paused)

If any required job is manual:

- Identify job name, stage, and URL.
- If the current request explicitly names and authorizes the job, play it and continue through terminal verification.
- Provide one of:
  - the exact “play” instruction (GitLab UI path), AND
  - the exact `glab` command (if supported in environment) OR the pipeline/job URL
- Explain what will happen after the manual job is played (next stage/child pipeline).

When the job is not pre-authorized, label the outcome as “Requires human action” and stop.

### 5) Debugging policy (multi-stage + artifacts + early-stage failures)

When any job fails OR downstream stages misbehave:

- Always pull logs for:
  - the first failing job
  - any upstream jobs that produce artifacts or state used downstream
  - terraform/tofu/terragrunt plan/apply jobs in earlier stages
- If artifacts influence behavior:
  - confirm artifact existence (download/list if feasible)
  - confirm artifact was produced by the expected commit/job

The agent must not focus only on the latest stage if earlier stage created the artifact/state.

### 6) Child/bridge pipeline policy

If pipelines trigger child/bridge pipelines:

- The agent must discover and report the child pipeline IDs and statuses.
- Failures in child pipelines are treated as failures of the overall deployment.
- Debugging must include failed child pipeline jobs and their logs.

### 7) Terraform/OpenTofu/Terragrunt workflow rules

- Prefer `plan` -> review -> `apply` discipline when pipeline uses manual apply.
- When diagnosing failures, extract and summarize:
  - provider errors (AWS auth/assume role/OIDC)
  - dependency graph issues (missing outputs/artifacts)
  - state lock issues
  - drift / import needs

If proposing a patch:

- Show exact file changes or commands.
- Explain why the change addresses the log evidence.
- Re-run pipeline and verify status (bounded polling).

### 8) AWS OIDC / role assumption checks (common failure class)

If logs include AssumeRoleWithWebIdentity/OIDC errors:

- Verify job has expected env vars and token file path (as shown in logs/CI config).
- Verify audience/subject/role ARN referenced.
- Check for expired token / incorrect AWS region / missing permissions in role policy.

Do not guess — cite exact log lines.

### 9) Output format (required for GitLab pipeline/deployment tasks)

Every GitLab pipeline/deployment response must end with a “Run Summary” block:

- Pipeline: <id> <url>
- Status: <success|failed|requires manual|timeout>
- Manual jobs blocking: <list or none>
- Failed jobs: <list or none>
- Child pipelines: <list>
- Next action: <none|play job X|apply patch Y|rerun pipeline>

