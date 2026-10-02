---
name: cos-update
description: >
  Use when starting, blocking, completing, or making significant progress on
  any work session on nyx. Sends a structured status update to
  chief-of-staff@ghost so CoS can maintain Kanban awareness without you
  having to touch the Kanban directly.
---

# CoS Update

Report your status to chief-of-staff@ghost. CoS receives the message,
updates the Ghost Kanban, and maintains awareness of all running work
across nyx workers.

---

## When to call this (do not wait to be asked)

- **Session start** — as soon as you understand what you're working on
- **Blocked** — the moment you hit a gate you can't pass autonomously
- **Review needed** — when an MR/PR is open and waiting on a human
- **Session end / significant completion** — before you stop working
- **Mid-session state change** — when something material shifts

---

## How to call it

Use `terminal` to run the command directly:

```
hermes peer dm ghost/chief-of-staff "[<profile>@nyx] [<event>] <message>"
```

Where `<event>` is one of: `started` | `blocked` | `review` | `completed` | `update`

### Examples

Session start:

```
hermes peer dm ghost/chief-of-staff "[ops@nyx] [started] Working on gitlab secret rotation governance#103. Will report on blockers and completion."
```

Blocked:

```
hermes peer dm ghost/chief-of-staff "[ops@nyx] [blocked] Blocked on inter-node-ssh access list decision (governance#103). Owner: Chris. Parallel: continuing T6-T9 runbook prep."
```

Review needed:

```
hermes peer dm ghost/chief-of-staff "[coder@nyx] [review] gitlab-ami-packer!21 ready for review. Pipeline green. Waiting on: maintainer merge approval."
```

Completed:

```
hermes peer dm ghost/chief-of-staff "[ops@nyx] [completed] GitLab secret rotation T1-T5 done. Merged: !166/!167/!168/!169/!172. Remaining: T6 (outage window needed), inter-node-ssh (owner decision). governance#103 updated."
```

---

## Message content

One paragraph, factual, no padding. Always include:

- What was done or what the state is
- GitLab/GitHub links when available (use full paths: `git.ap.org/...#N`)
- Who owns any gate or decision
- What parallel work can continue

CoS does not have access to `git.ap.org` — if you reference AP GitLab issues,
use the full URL so CoS can record them correctly without needing direct access.

---

## Pitfalls

- Do not skip this because the session was "just a quick fix" — CoS needs
  the completed signal to update the Kanban and know the task is done.
- Do not send a wall of text. CoS extracts the key facts; keep it tight.
- If `hermes peer dm` fails (ghost unreachable), log it and move on —
  do not block your actual work on the status report.
