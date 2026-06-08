{ lib, pkgs, ... }:
{
  networking.hostName = "VNJTECMBCD";

  system.stateVersion = 6;
  system.primaryUser = "cdenneen";

  home-manager.users.cdenneen = {
    programs.starship.settings.palette = lib.mkForce "VNJTECMBCD";
    launchd.agents.jarvis-mac-runner.enable = lib.mkForce false;

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
    pkgs.podman
    pkgs.bash
  ];

  launchd.user.agents."net.denneen.jarvis.mac-worker" = {
    command = ''
      ${pkgs.bash}/bin/bash -lc '
        set -euo pipefail
        image="''${JARVIS_WORK_RUNNER_CONTAINER_IMAGE:-localhost/jarvis-work-runner:latest}"
        runtime_dir="$HOME/Library/Application Support/jarvis"
        data_dir="$runtime_dir/data"
        env_file="$runtime_dir/work-runner.env"
        dev_env_file="$runtime_dir/dev.env"
        repo_dir="''${JARVIS_REPO_DIR:-$HOME/code/workspace/personal/jarvis}"

        mkdir -p "$data_dir"

        shared_token="''${JARVIS_SHARED_TOKEN:-}"

        if [ ! -r "$env_file" ]; then
          {
              printf '%s\n' "JARVIS_WORK_BIND=0.0.0.0:8091"
              printf '%s\n' "JARVIS_WORKER_ID=mac-worker-1"
              printf '%s\n' "JARVIS_WORKER_CAPABILITIES=code,triage,documentation,investigation"
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

        harness_url="''${JARVIS_HARNESS_URL:-http://100.114.242.29:8079}"
        registration_token="''${JARVIS_WORKER_REGISTRATION_TOKEN:-}"
        public_endpoint="''${JARVIS_WORKER_PUBLIC_ENDPOINT:-http://100.90.97.48:8091}"
        worker_realm="''${JARVIS_WORKER_REALM:-work}"
        callback_url="''${JARVIS_WORK_STATUS_CALLBACK_URL:-http://100.114.242.29:8080/api/tasks/worker-update}"

        ${pkgs.podman}/bin/podman machine start >/dev/null 2>&1 || true
        env_args=(--env-file "$env_file")
        if [ -r "$dev_env_file" ]; then
          env_args+=(--env-file "$dev_env_file")
        fi
        ${pkgs.podman}/bin/podman rm -f jarvis-mac-worker >/dev/null 2>&1 || true
        exec ${pkgs.podman}/bin/podman run --rm --name jarvis-mac-worker -p 8091:8091 \
          -v "$repo_dir:/opt/jarvis" \
          -v "$runtime_dir:/var/lib/jarvis" \
          "''${env_args[@]}" \
          "$image" \
          --host 0.0.0.0 \
          --port 8091 \
          --shared-token "$shared_token" \
          --callback-url "$callback_url" \
          --callback-token "$shared_token" \
          --worker-id "''${JARVIS_WORKER_ID:-mac-worker-1}" \
          --capabilities "''${JARVIS_WORKER_CAPABILITIES:-code,triage,documentation,investigation}" \
          --harness-url "$harness_url" \
          --registration-token "$registration_token" \
          --public-endpoint "$public_endpoint" \
          --worker-realm "$worker_realm" \
          --state-file /var/lib/jarvis/data/work-runner-state.json \
          --repo-dir /opt/jarvis
      '
    '';
    serviceConfig = {
      KeepAlive = true;
      RunAtLoad = true;
      StandardOutPath = "/Users/cdenneen/Library/Logs/jarvis-mac-worker.log";
      StandardErrorPath = "/Users/cdenneen/Library/Logs/jarvis-mac-worker.err.log";
    };
  };
}
