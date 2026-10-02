# Kanban shim

FastMCP server on `eros` that exposes Ghost's Hermes Kanban as an MCP toolset.

## Trust boundary

**Critical:** This shim can *only* read/write the ghost Kanban database via the constrained SSH command. It cannot:

- Execute arbitrary commands on ghost
- Read/write any files on ghost other than the Kanban SQLite database
- Access other services on ghost (PostgreSQL, RabbitMQ, etc.)
- Modify systemd services or any system config on ghost

The SSH connection is constrained via a forced-command in `authorized_keys` on ghost:

```bash
command="hermes kanban" ssh-rsa AAAAB3... kanban-shim@eros
```

All commands pass through `hermes kanban <verb> ...`, which is the only executable path allowed.

## SSH forced-command setup

To constrain the SSH key to `hermes kanban` operations only:

1. Generate a deploy key on `eros`:
   ```bash
   ssh-keygen -t ed25519 -f /run/kanban-shim/ssh_key -C "kanban-shim@eros"
   ```

2. Copy the public key (`/run/kanban-shim/ssh_key.pub`) to `ghost` and add it to `~/.ssh/authorized_keys` with the `command` prefix:
   ```bash
   command="hermes kanban" ssh-ed25519 AAAAC3... kanban-shim@eros
   ```
   This ensures that even if the private key is compromised, the attacker can only execute `hermes kanban ...` subcommands, not arbitrary shell commands.

3. Set proper permissions on `eros`:
   ```bash
   install -m 0400 /run/kanban-shim/ssh_key ~/.ssh/id_ed25519
   chmod 0600 ~/.ssh/authorized_keys
   ```

## Tool surface

### kanban_create

Create a new task.

```python
kanban_create(
    title: str,
    assignee: str,
    body: str | None = None,
    board: str | None = None,
    parents: list[str] | None = None,
) -> dict | list[dict]
```

Returns the created task (or list if --json returns list).

### kanban_list

List tasks.

```python
kanban_list(
    board: str | None = None,
    status: str | None = None,
    assignee: str | None = None,
) -> list[dict]
```

Returns a list of tasks matching the filter criteria.

### kanban_update

Update a task status and optionally append a comment.

```python
kanban_update(
    task_id: str,
    status: str | None = None,
    comment: str | None = None,
) -> dict | list[dict]
```

Supported status values: `done`, `blocked`, `review`, `archived`, `scheduled`.
For `todo`, `ready`, or `running`, use `kanban_list` to verify or call `kanban_show`.

Returns the updated task (full `kanban show` output).

### kanban_comment

Append a comment to a task.

```python
kanban_comment(
    task_id: str,
    text: str,
) -> dict
```

Returns `{"status": "commented", "task_id": "<id>"}`.

### kanban_show

Show a task with comments + events.

```python
kanban_show(
    task_id: str,
) -> dict
```

Returns full task details.

### health

Service health check.

```python
health() -> dict
```

Returns: `{"status": "ok", "ssh_target": "...", "ssh_key_set": true|false, "port": ...}`

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `KANBAN_SHIM_PORT` | `18124` | FastMCP server port |
| `KANBAN_SHIM_SSH_TARGET` | `cdenneen@ghost.tail0e55.ts.net` | SSH target host |
| `KANBAN_SHIM_SSH_KEY` | `/run/kanban-shim/ssh_key` | Path to SSH private key |

## Deployment

See `hosts/nixos/eros.nix`: the service runs as `cdenneen`, installs the SSH key via
`ExecStartPre` with `0400` permissions, and enforces `NoNewPrivileges=true`.

The `ExecStartPre` script uses `install -m 600` to copy the SOPS-decrypted key to
`/run/kanban-shim/ssh_key`.
