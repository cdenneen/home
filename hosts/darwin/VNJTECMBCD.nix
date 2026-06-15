{ lib, pkgs, ... }:
{
  networking.hostName = "VNJTECMBCD";

  system.stateVersion = 6;
  system.primaryUser = "cdenneen";

  home-manager.users.cdenneen = {
    programs.starship.settings.palette = lib.mkForce "VNJTECMBCD";

    # Tokyo Night (TUI focus) for Ghostty + Neovim.
    xdg.configFile."ghostty/themes/tokyonight-night".text = ''
      background = 1a1b26
      foreground = c0caf5
      cursor-color = c0caf5
      selection-background = 33467c
      selection-foreground = c0caf5

      # ANSI colors
      palette = 0=15161e
      palette = 1=f7768e
      palette = 2=9ece6a
      palette = 3=e0af68
      palette = 4=7aa2f7
      palette = 5=bb9af7
      palette = 6=7dcfff
      palette = 7=a9b1d6
      palette = 8=414868
      palette = 9=f7768e
      palette = 10=9ece6a
      palette = 11=e0af68
      palette = 12=7aa2f7
      palette = 13=bb9af7
      palette = 14=7dcfff
      palette = 15=c0caf5
    '';

    # Override the shared Ghostty config to use Tokyo Night.
    xdg.configFile."ghostty/config".text = lib.mkForce ''
      background-opacity = 0.8
      font-family = JetBrainsMono Nerd Font Mono
      theme = tokyonight-night
      command = ${lib.getExe pkgs.zsh}
      confirm-close-surface = false
      quit-after-last-window-closed = true
    '';

  };

  environment.systemPackages = [
    pkgs.bash
    pkgs.nodejs_24
    pkgs.pnpm
    pkgs.podman
    pkgs.uv
  ];

  launchd.user.agents."net.denneen.jarvis.mac-worker" = {
    command = ''
      ${pkgs.bash}/bin/bash -lc '
        set -euo pipefail
        runtime_dir="$HOME/Library/Application Support/jarvis"
        data_dir="$runtime_dir/data"
        env_file="$runtime_dir/work-runner.env"
        dev_env_file="$runtime_dir/dev.env"
        repo_dir="''${JARVIS_REPO_DIR:-$HOME/code/workspace/personal/jarvis}"
        runner_path="$repo_dir/services/jarvis-work-runner-service.py"
        uv_bin="${pkgs.uv}/bin/uv"
        pnpm_bin="${pkgs.pnpm}/bin/pnpm"

        mkdir -p "$data_dir"

        shared_token="''${JARVIS_SHARED_TOKEN:-}"

        if [ ! -r "$env_file" ]; then
          {
              printf '%s\n' "JARVIS_WORK_BIND=0.0.0.0:8091"
              printf '%s\n' "JARVIS_WORKER_ID=mac-worker-1"
              printf '%s\n' "JARVIS_WORKER_CAPABILITIES=code,triage,documentation,investigation"
              printf '%s\n' "JARVIS_WORKER_REALM=work"
              printf '%s\n' "JARVIS_HARNESS_URL=http://100.114.242.29:8079"
              printf '%s\n' "JARVIS_WORK_STATUS_CALLBACK_URL=http://100.114.242.29:8080/api/tasks/worker-update"
              if [ -n "$shared_token" ]; then
              printf 'JARVIS_SHARED_TOKEN=%s\n' "$shared_token"
              printf 'JARVIS_WORKER_REGISTRATION_TOKEN=%s\n' "$shared_token"
              fi
          } > "$env_file"
          chmod 0600 "$env_file"
        fi

        if [ -r "$env_file" ]; then
          # shellcheck disable=SC1090
          source "$env_file"
        fi
        if [ -r "$dev_env_file" ]; then
          # shellcheck disable=SC1090
          source "$dev_env_file"
        fi

        if [ ! -f "$runner_path" ]; then
          echo "jarvis-mac-worker: missing entrypoint at $runner_path" >&2
          exit 1
        fi

        if [ -f "$repo_dir/pyproject.toml" ]; then
          "$uv_bin" sync --project "$repo_dir"
        fi

        if [ -f "$repo_dir/package.json" ] && [ ! -d "$repo_dir/node_modules" ]; then
          "$pnpm_bin" install --frozen-lockfile || "$pnpm_bin" install
        fi

        work_bind="''${JARVIS_WORK_BIND:-0.0.0.0:8091}"
        work_host="''${work_bind%:*}"
        work_port="''${work_bind##*:}"

        public_endpoint="''${JARVIS_WORKER_PUBLIC_ENDPOINT:-}"
        if [ -z "$public_endpoint" ]; then
          tailscale_cmd=""
          if command -v tailscale >/dev/null 2>&1; then
            tailscale_cmd="$(command -v tailscale)"
          elif [ -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ]; then
            tailscale_cmd=/Applications/Tailscale.app/Contents/MacOS/Tailscale
          fi
          if [ -n "$tailscale_cmd" ]; then
            tailscale_ip="$($tailscale_cmd ip -4 | head -n 1)"
          else
            tailscale_ip=""
          fi
          if [ -n "$tailscale_ip" ]; then
            public_endpoint="http://$tailscale_ip:$work_port"
          else
            public_endpoint="http://127.0.0.1:$work_port"
          fi
        fi

        exec "$uv_bin" run --project "$repo_dir" -- python "$runner_path" \
          --host "$work_host" \
          --port "$work_port" \
          --shared-token "''${JARVIS_SHARED_TOKEN:-}" \
          --callback-url "''${JARVIS_WORK_STATUS_CALLBACK_URL:-http://100.114.242.29:8080/api/tasks/worker-update}" \
          --callback-token "''${JARVIS_SHARED_TOKEN:-}" \
          --worker-id "''${JARVIS_WORKER_ID:-mac-worker-1}" \
          --capabilities "''${JARVIS_WORKER_CAPABILITIES:-code,triage,documentation,investigation}" \
          --harness-url "''${JARVIS_HARNESS_URL:-http://100.114.242.29:8079}" \
          --registration-token "''${JARVIS_WORKER_REGISTRATION_TOKEN:-}" \
          --registration-token-file "$data_dir/worker-registration-token.txt" \
          --public-endpoint "$public_endpoint" \
          --worker-realm "''${JARVIS_WORKER_REALM:-work}" \
          --state-file "$data_dir/work-runner-state.json" \
          --repo-dir "$repo_dir"
      '
    '';
    serviceConfig = {
      KeepAlive = true;
      RunAtLoad = true;
      StandardOutPath = "/Users/cdenneen/Library/Logs/jarvis-node.log";
      StandardErrorPath = "/Users/cdenneen/Library/Logs/jarvis-node.err.log";
    };
  };
}
