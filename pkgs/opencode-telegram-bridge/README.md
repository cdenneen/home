# opencode-telegram-bridge

Telegram bridge for OpenCode. This package ships the `opencode-telegram-bridge`
binary and is configured via Home Manager with `programs.telegram-bridge`.

## Home Manager options

Enable the service:

```nix
programs.telegram-bridge.enable = true;
```

### Telegram settings

- `programs.telegram-bridge.telegram.botTokenFile`
  Path to the sops-nix secret containing the bot token.
- `programs.telegram-bridge.telegram.ownerChatIdFile`
  Path to the sops-nix secret containing the owner chat id (used for pairing).
- `programs.telegram-bridge.telegram.allowedChatIds`
  List of chat ids allowed to use the bot. When empty, the bridge falls back to
  the DB-stored list (or the owner chat id).
- `programs.telegram-bridge.telegram.allowedChatIdsFile`
  Optional file containing a comma-separated list of allowed chat ids.
- `programs.telegram-bridge.telegram.updatesMode`
  `"polling"` or `"webhook"`.
  - `polling`: the bridge calls `getUpdates` from Telegram (no public webhook).
  - `webhook`: Telegram posts updates to your public webhook URL.
- `programs.telegram-bridge.telegram.pollTimeoutSec`
  Long-poll duration for `getUpdates` when using polling.
- `programs.telegram-bridge.telegram.dbRetentionDays`
  Prune topic rows older than this many days (0 disables).
- `programs.telegram-bridge.telegram.dbMaxTopics`
  Max topic rows retained in the DB (0 disables).
- `programs.telegram-bridge.telegram.webhook.listenHost`
  Host to bind the local webhook listener (default `127.0.0.1`).
- `programs.telegram-bridge.telegram.webhook.listenPort`
  Port for the local webhook listener (default `18080`).
- `programs.telegram-bridge.telegram.webhook.path`
  HTTP path for webhook requests (default `/telegram`).
- `programs.telegram-bridge.telegram.webhook.publicUrl`
  Public base URL (scheme+host) that Telegram calls (e.g. `https://nyx.denneen.net`).
- `programs.telegram-bridge.telegram.webhook.fallbackSec`
  If > 0, fall back to polling when webhook idle for this many seconds.

### OpenCode settings

- `programs.telegram-bridge.opencode.workspaceRoot`
  Default workspace root for `/map <name>`.
- `programs.telegram-bridge.opencode.bin`
  Path to the `opencode` executable.
- `programs.telegram-bridge.opencode.maxSessions`
  Max concurrent opencode sessions.
- `programs.telegram-bridge.opencode.idleTimeoutSec`
  Time before idle sessions are evicted.
- `programs.telegram-bridge.opencode.defaultModel`
  Optional default model (can be changed at runtime via `/model <id>`).
- `programs.telegram-bridge.opencode.defaultAgent`
  Optional default agent.
- `programs.telegram-bridge.opencode.defaultProvider`
  Provider prefix for models without a provider (default `openai`).

### User override file

The bridge merges an optional user override JSON file into the generated config:

- Default: `~/.config/telegram_bridge/config.user.json`
- Override path via `OPENCODE_TELEGRAM_CONFIG_USER`

This is useful for ad-hoc changes without re-generating the base config.

## opencode-chat helper

The package includes `opencode-chat`, a small helper that attaches your local
TUI to the most recently used bridge session (based on the bridge DB). It runs:

```
opencode attach http://127.0.0.1:<port> --session <id>
```

Each time you run it, the chosen topic is marked as the most recent, so the
next run attaches to the same session by default.

## Access policy notes

If you expose `/chat` via Cloudflare Access, configure the Access policy to
only allow your GitHub user(s). You can document the intended allow-list in
Home Manager via:

```
programs.telegram-bridge.chat.allowedGithubUsers = [ "cdenneen" ];
```
