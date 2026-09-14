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
