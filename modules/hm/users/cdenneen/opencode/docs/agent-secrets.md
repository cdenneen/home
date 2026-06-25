# Agent Secrets (sops-nix + age)

## Files

- Encrypted: `secrets/secrets.yaml`
- Recipients config: `.sops.yaml`
- Human registry: `pub/age-recipients.txt`

## Devshell helpers (preferred)

```sh
nix develop
sops-edit
sops-diff-keys
sops-update-keys   # non-interactive
sops-verify-keys
sops-bootstrap-host
```

## Safe recipient rotation

1. Bootstrap host key: `sops-bootstrap-host`
2. Update `pub/age-recipients.txt` and `.sops.yaml`
3. Re-encrypt: `sops-update-keys`
4. Verify registry: `sops-verify-keys`

## Host key conventions (Linux)

- System key: `/var/sops/age/keys.txt`
- Permissions: `root:sops` + directory `0750`, file `0440` so user sops-nix can read.

If you add a user to the `sops` group after login, the systemd user manager may not pick up the new supplementary groups. Fix by re-logging in or restarting the user manager:

```sh
sudo systemctl restart user@$(id -u <user>).service
```
