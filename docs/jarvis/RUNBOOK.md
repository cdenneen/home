# Jarvis Deploy Runbook

## Topology

- `ghost`: personal control plane
  - `jarvis-harness`
  - `jarvis-api` on `127.0.0.1:8080`
  - `jarvis-slack-gateway` on `127.0.0.1:8081`
  - `jarvis-web` on `127.0.0.1:3000`
  - `jarvis-brain-sync` timer/service (20m)
  - `cloudflared`
  - `tailscaled`
- `nyx`: work execution runner
  - `jarvis-node` on `0.0.0.0:8091` (`/workers` worker contract)
  - `tailscaled`
- local Mac: node runner
  - `jarvis-node` on `vnjtecmbcd.tail0e55.ts.net:8091`

## Source Repos

- App repo: `git@gitlab.com:cdenneen/my-jarvis.git`
- Flake repo: `~/src/workspace/nix/home` on Linux, `~/code/workspace/nix/home` on Mac

## Repo Checkout Layout

- `ghost`: `/opt/jarvis`
- `nyx`: `/opt/jarvis`
- local Mac: `~/code/workspace/personal/jarvis`

## Deploy Workflow

1. Commit and push app changes to `git@gitlab.com:cdenneen/my-jarvis.git`.
2. Commit and push flake changes to the `nix/home` repo.
3. Pull the app repo on the target host.
4. Pull the flake repo on the target host.
5. Apply the host profile.
6. Verify systemd or launchd health and endpoint health.

Hosts use the flake-managed `cdenneen_ed25519_2024` SSH key for GitLab.com app checkout and pull operations.

## Ghost Deploy

```sh
ssh ghost
cd /opt/jarvis
git pull --rebase

cd ~/src/workspace/nix/home
git pull --rebase
sudo nixos-rebuild switch --flake .#ghost

systemctl status jarvis-harness jarvis-api jarvis-slack-gateway jarvis-web --no-pager
systemctl status jarvis-brain-sync.timer jarvis-brain-sync.service --no-pager
curl -fsS http://127.0.0.1:8080/healthz
curl -fsS http://127.0.0.1:8081/healthz
curl -fsS http://127.0.0.1:3000 >/dev/null
```

## Nyx Deploy

```sh
ssh nyx
cd /opt/jarvis
git pull --rebase

cd ~/src/workspace/nix/home
git pull --rebase
sudo nixos-rebuild switch --flake .#nyx

systemctl status jarvis-node --no-pager
curl -fsS http://127.0.0.1:8091/healthz
curl -fsS http://127.0.0.1:8091/workers
```

## Local Mac Deploy

```sh
cd ~/code/workspace/nix/home
git pull --rebase
home-manager switch --flake .#cdenneen@VNJTECMBCD

launchctl list | grep jarvis-node
tail -n 100 ~/Library/Logs/jarvis-node.log
```

## Public Ingress

- `ai.denneen.net/api/*` -> `http://localhost:8080`
- `ai.denneen.net/ws/*` -> `http://localhost:8080`
- `ai.denneen.net/slack/events` -> `http://localhost:8081`
- `ai.denneen.net/` -> `http://localhost:3000`

## Internal Work Runner Path

- `ghost` calls `http://nyx.tail0e55.ts.net:8091`
- `nyx` exposes `8091` only on `tailscale0`
- `ghost` calls `http://vnjtecmbcd.tail0e55.ts.net:8091` for `personal-local` actions

## Validation Checklist

- `ghost`
  - `systemctl is-active jarvis-harness jarvis-api jarvis-slack-gateway jarvis-web`
  - `systemctl is-active jarvis-brain-sync.timer`
  - `curl -fsS http://127.0.0.1:8080/healthz`
  - `curl -fsS http://127.0.0.1:8081/healthz`
  - `curl -fsS http://127.0.0.1:3000 >/dev/null`
- `nyx`
  - `systemctl is-active jarvis-node`
  - `curl -fsS http://127.0.0.1:8091/healthz`
  - `curl -fsS http://127.0.0.1:8091/workers`
  - `curl -fsS http://nyx.tail0e55.ts.net:8091/healthz` from `ghost`
- local Mac
  - `launchctl list | grep jarvis-node`
  - `curl -fsS http://vnjtecmbcd.tail0e55.ts.net:8091/healthz`
- public path checks
  - `https://ai.denneen.net/api/healthz`
  - websocket connect to `wss://ai.denneen.net/ws/voice`
  - Slack Events URL set to `https://ai.denneen.net/slack/events`

## Helpers

- `deploy-jarvis ghost`
- `deploy-jarvis nyx`
- `deploy-jarvis mac`
