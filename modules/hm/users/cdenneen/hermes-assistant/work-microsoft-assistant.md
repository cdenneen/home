---
name: work-microsoft-assistant
description: Read-only Outlook Mail, Calendar, and Teams access for assistant@nyx through the approved custom enterprise app.
platforms: [linux]
---

# Work Microsoft Assistant

Use `hermes-msgraph` for Microsoft 365 access through Chris's custom enterprise app. Do not configure or use Hermes native Microsoft Enterprise Apps, Teams bot registrations, application permissions, or webhook delivery.

- Health: `hermes-msgraph status`
- Identity: `hermes-msgraph me`
- Mail: `hermes-msgraph mail --max 10`
- Calendar: `hermes-msgraph calendar --days 7 --max 20`
- Teams chats: `hermes-msgraph teams-chats --max 10`
- Teams messages: `hermes-msgraph teams-messages CHAT_ID --max 20`

All commands are read-only. Never send, reply, move, delete, categorize, schedule, edit, or post. Keep all returned content in the Nyx work trust domain; send Chief of Staff only the minimum status or task metadata needed for coordination.

## Daily Brief Workflow

The governed work brief runs Monday through Friday at 08:00 America/New_York and delivers to Chris's existing Nyx coder-bot direct message.

1. List today's calendar in time order.
2. Identify overlaps or tight transitions within 48 hours.
3. Select at most five unread or time-sensitive mail items requiring Chris's action.
4. Include only Teams items that require Chris's action.
5. Produce a short, deduplicated action list.

Do not include raw message bodies, provider object IDs, unrelated corporate content, or credentials. Slack delivery is reporting authority only; it does not grant permission to mutate Outlook, Calendar, or Teams.

## Health and Soak

`hermes-assistant-health.timer` performs an hourly read-only Graph probe and records only check names/status under `~/.local/state/hermes-assistant`. The first 48 hours are the acceptance soak. Slack receives the start result and later health transitions, not mail, calendar, or Teams probe output.
