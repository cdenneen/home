# Deployment Guide

1. Merge the home-flake change and verify CI.
2. Run `home-manager build --flake .#cdenneen@ghost`.
3. Pause existing supervisor cron jobs and snapshot Hermes state.
4. Set current control to `mode=observing` and
   `allow_repository_mutation=false` before activation.
5. Run `home-manager switch --flake .#cdenneen@ghost`.
6. Explicitly restart `hermes-gateway.service` and start/restart
   `hermes-supervisor-cron.service`.
7. Verify the single managed gateway service and Slack connection.
8. Validate schemas/scripts and run isolated fault fixtures.
9. Start in `observing`; enable mutation only after inventory and lease checks.

Rollback:

```bash
home-manager generations
/nix/store/<previous-generation>/activate
systemctl --user daemon-reload
systemctl --user restart hermes-gateway.service
```

If returning to Hermes-managed service files rather than a Home Manager
generation, use this exact first-rollout restoration order after activating the
previous generation:

```bash
mkdir -p ~/.config/systemd/user/hermes-gateway.service.d
if [ -f ~/.config/systemd/user/hermes-gateway.service.pre-home-manager ]; then
  mv -f ~/.config/systemd/user/hermes-gateway.service.pre-home-manager \
    ~/.config/systemd/user/hermes-gateway.service
fi
if [ -f ~/.config/systemd/user/hermes-gateway.service.d/override.conf.pre-home-manager ]; then
  mv -f ~/.config/systemd/user/hermes-gateway.service.d/override.conf.pre-home-manager \
    ~/.config/systemd/user/hermes-gateway.service.d/override.conf
fi
backup=~/.hermes/supervisor/axis-development-supervisor/migration-backup-1.0.0
if [ -d "$backup" ]; then
  cp -a "$backup"/. "$HOME"/
fi
systemctl --user daemon-reload
systemctl --user restart hermes-gateway.service
```

Confirm the legacy unit's `ExecStart`, Slack connection, and paused cron state.
Restore the Hermes state snapshot separately only when runtime state rollback is
required.
