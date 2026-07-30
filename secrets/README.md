# Encrypted Secret Operations

`secrets/secrets.yaml` is SOPS-encrypted. Never place plaintext credentials in
the repository, shell history, command arguments, logs, GitLab/GitHub issues,
or acceptance evidence.

## Read One Value

```bash
sops -d --extract '["secret_key_name"]' secrets/secrets.yaml
```

This prints the selected value to the current terminal. Do not redirect it to a
repository file.

## Edit Interactively

```bash
sops secrets/secrets.yaml
```

## Set From Standard Input

Prefer stdin so the value does not appear in process arguments:

```bash
read -rs VALUE
printf '%s' "$VALUE" | jq -Rs . \
  | sops set --value-stdin secrets/secrets.yaml '["secret_key_name"]'
unset VALUE
```

## Rekey

After changing `.sops.yaml` recipients:

```bash
sops updatekeys secrets/secrets.yaml
```

## Rotation Procedure

1. Create the replacement credential at the provider.
2. Write it to SOPS through stdin.
3. Commit and merge only the encrypted file.
4. Rebuild or restart every declared consumer.
5. Verify the replacement works.
6. Revoke the old credential.
7. Review provider audit logs.
8. Record timestamps and verification in the confidential security issue,
   never the credential value.

## AXIS Secrets

AXIS-related keys use the `axis_` prefix. Provider API keys, voice IDs, model
routing configuration, dashboard credentials, and external-vault bootstrap
references remain installation-local. Portable Organism exports contain only
approved semantic intent and references, never these values.
