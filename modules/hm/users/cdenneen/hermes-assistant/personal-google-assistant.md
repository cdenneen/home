---
name: personal-google-assistant
description: Read-only personal Gmail and Google Calendar access for assistant@ghost.
platforms: [linux]
---

# Personal Google Assistant

Use `hermes-google-workspace` for the personal Gmail and Google Calendar account.

- Mail: `hermes-google-workspace gmail search "is:unread" --max 10`
- Message: `hermes-google-workspace gmail get MESSAGE_ID`
- Calendar: `hermes-google-workspace calendar list`

The OAuth grant is read-only. Never attempt send, reply, label, delete, or calendar mutation commands. Keep message and calendar content in the personal Ghost trust domain.

## Daily Brief Workflow

The governed personal brief runs daily at 07:30 America/New_York and delivers to the existing Ghost home Slack channel.

1. List today's calendar in time order.
2. Identify overlaps or tight transitions within 48 hours.
3. Select at most five unread messages that are time-sensitive or require Chris's action.
4. Produce a short, deduplicated action list.

Do not include raw message bodies, provider object IDs, unrelated private content, or credentials. Slack delivery is reporting authority only; it does not grant permission to mutate Gmail or Calendar.

## Health and Soak

`hermes-assistant-health.timer` performs an hourly read-only probe and records only check names/status under `~/.local/state/hermes-assistant`. The first 48 hours are the acceptance soak. Slack receives the start result and later health transitions, not mailbox or calendar probe output.
