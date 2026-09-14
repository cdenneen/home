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
