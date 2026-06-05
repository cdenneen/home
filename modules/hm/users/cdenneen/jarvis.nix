{
  config,
  lib,
  pkgs,
  ...
}:
let
  isDarwin = pkgs.stdenv.isDarwin;
  homeDir = config.home.homeDirectory;
  jarvisRuntimeDir = "${homeDir}/Library/Application Support/jarvis";
  jarvisEnvFile = "${jarvisRuntimeDir}/voice-edge.env";
  jarvisMacRunnerEnvFile = "${jarvisRuntimeDir}/mac-runner.env";
  jarvisStateFile = "${jarvisRuntimeDir}/voice-edge-state.json";
  jarvisMacRunnerStateFile = "${jarvisRuntimeDir}/mac-runner-state.json";
  jarvisVoiceUiPath = "${homeDir}/code/workspace/personal/jarvis/jarvis-voice-ui.html";
  jarvisLogFile = "${homeDir}/Library/Logs/jarvis-voice-edge.log";
  jarvisMacRunnerLogFile = "${homeDir}/Library/Logs/jarvis-mac-runner.log";
  jarvisVoiceUiLogFile = "${homeDir}/Library/Logs/jarvis-voice-ui.log";
  jarvisMacRunnerScript = pkgs.writeShellScript "jarvis-mac-runner" ''
    set -euo pipefail

    env_file=${lib.escapeShellArg jarvisMacRunnerEnvFile}
    runner_path=${lib.escapeShellArg "${homeDir}/code/workspace/personal/jarvis/src/jarvis/mac_runner.py"}
    state_file_default=${lib.escapeShellArg jarvisMacRunnerStateFile}

    if [ -r "$env_file" ]; then
      # shellcheck disable=SC1090
      source "$env_file"
    fi

    if [ ! -f "$runner_path" ]; then
      echo "jarvis-mac-runner: missing app repo entrypoint at $runner_path" >&2
      exit 1
    fi

    if command -v tailscale >/dev/null 2>&1; then
      tailscale_cmd="$(command -v tailscale)"
    elif [ -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ]; then
      tailscale_cmd=/Applications/Tailscale.app/Contents/MacOS/Tailscale
    else
      echo "jarvis-mac-runner: tailscale CLI not found" >&2
      exit 1
    fi

    tailscale_ip="$("$tailscale_cmd" ip -4 | head -n 1)"
    if [ -z "$tailscale_ip" ]; then
      echo "jarvis-mac-runner: unable to determine tailscale IPv4 address" >&2
      exit 1
    fi

    exec ${jarvisRunnerPython}/bin/python "$runner_path" \
      --host "$tailscale_ip" \
      --port "''${JARVIS_MAC_RUNNER_PORT:-8091}" \
      --shared-token "''${JARVIS_MAC_RUNNER_SHARED_TOKEN:-}" \
      --state-file "''${JARVIS_MAC_RUNNER_STATE_FILE:-$state_file_default}"
  '';
  jarvisPython = pkgs.python3.withPackages (
    ps: with ps; [
      websockets
    ]
  );
  jarvisRunnerPython = pkgs.python3.withPackages (
    ps: with ps; [
      fastapi
      uvicorn
    ]
  );
in
lib.mkIf isDarwin {
  home.file.".local/bin/jarvis-voice-edge-status" = {
    executable = true;
    text = ''
      #!/usr/bin/env bash
      set -euo pipefail

      state_file=${lib.escapeShellArg jarvisStateFile}
      if [ ! -r "$state_file" ]; then
        echo "jarvis-voice-edge: no state file at $state_file" >&2
        exit 1
      fi

      cat "$state_file"
    '';
  };

  home.file.".local/bin/jarvis-mac-runner-status" = {
    executable = true;
    text = ''
      #!/usr/bin/env bash
      set -euo pipefail

      state_file=${lib.escapeShellArg jarvisMacRunnerStateFile}
      if [ ! -r "$state_file" ]; then
        echo "jarvis-mac-runner: no state file at $state_file" >&2
        exit 1
      fi

      cat "$state_file"
    '';
  };

  home.file.".local/bin/jarvis-voice-ui-open" = {
    executable = true;
    text = ''
      #!/usr/bin/env bash
      set -euo pipefail

      ui_file=${lib.escapeShellArg jarvisVoiceUiPath}
      ui_mode=''${JARVIS_VOICE_UI_MODE:-window}

      if [ ! -f "$ui_file" ]; then
        echo "jarvis-voice-ui-open: missing ui file at $ui_file" >&2
        exit 1
      fi

      if [ "$ui_mode" = "menu-bar" ]; then
        # Placeholder mode for future menu-bar app implementation.
        open "$ui_file"
        exit 0
      fi

      if [ -d "/Applications/Google Chrome.app" ]; then
        open -a "Google Chrome" "$ui_file"
      else
        open "$ui_file"
      fi
    '';
  };

  home.activation.jarvisVoiceEdgeEnv = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    set -euo pipefail

    runtime_dir=${lib.escapeShellArg jarvisRuntimeDir}
    env_file=${lib.escapeShellArg jarvisEnvFile}
    tmp_env="$runtime_dir/voice-edge.env.tmp"

    mkdir -p "$runtime_dir"
    cat > "$tmp_env" <<'EOF'
JARVIS_VOICE_WS_URL=wss://ai.denneen.net/ws/voice
JARVIS_WAKE_PHRASE=Let's get to work Jarvis
JARVIS_TTS_MODE=remote_text_local_tts
JARVIS_TTS_VOICE_PROFILE=british-ai-assistant
JARVIS_VOICE_EDGE_STATE_FILE=${jarvisStateFile}
JARVIS_VOICE_RECONNECT_SECONDS=5
JARVIS_VOICE_HEARTBEAT_SECONDS=20
EOF
    chmod 600 "$tmp_env"
    mv -f "$tmp_env" "$env_file"

    runner_env=${lib.escapeShellArg jarvisMacRunnerEnvFile}
    tmp_runner_env="$runtime_dir/mac-runner.env.tmp"
    cat > "$tmp_runner_env" <<'EOF'
JARVIS_MAC_RUNNER_PORT=8091
JARVIS_MAC_RUNNER_SHARED_TOKEN=
JARVIS_MAC_RUNNER_STATE_FILE="${jarvisMacRunnerStateFile}"
EOF
    chmod 600 "$tmp_runner_env"
    mv -f "$tmp_runner_env" "$runner_env"
  '';

  launchd.agents.jarvis-voice-edge = {
    enable = true;
    config = {
      ProgramArguments = [
        "${jarvisPython}/bin/python"
        "${../../../shared/files/jarvis-voice-edge.py}"
        "--env-file"
        jarvisEnvFile
      ];
      EnvironmentVariables = {
        HOME = homeDir;
        PATH = "${config.home.profileDirectory}/bin:${config.home.homeDirectory}/.nix-profile/bin:/nix/var/nix/profiles/default/bin:/run/current-system/sw/bin:/etc/profiles/per-user/cdenneen/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin";
      };
      KeepAlive = true;
      RunAtLoad = true;
      ProcessType = "Background";
      StandardOutPath = jarvisLogFile;
      StandardErrorPath = jarvisLogFile;
    };
  };

  launchd.agents.jarvis-mac-runner = {
    enable = true;
    config = {
      ProgramArguments = [
        "${jarvisMacRunnerScript}"
      ];
      EnvironmentVariables = {
        HOME = homeDir;
        PATH = "${config.home.profileDirectory}/bin:${config.home.homeDirectory}/.nix-profile/bin:/nix/var/nix/profiles/default/bin:/run/current-system/sw/bin:/etc/profiles/per-user/cdenneen/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin";
      };
      KeepAlive = true;
      RunAtLoad = true;
      ProcessType = "Background";
      StandardOutPath = jarvisMacRunnerLogFile;
      StandardErrorPath = jarvisMacRunnerLogFile;
    };
  };

  launchd.agents.jarvis-voice-ui-autostart = {
    enable = true;
    config = {
      ProgramArguments = [
        "/bin/bash"
        "-lc"
        "sleep 10; ${homeDir}/.local/bin/jarvis-voice-ui-open"
      ];
      EnvironmentVariables = {
        HOME = homeDir;
        PATH = "${config.home.profileDirectory}/bin:${config.home.homeDirectory}/.nix-profile/bin:/nix/var/nix/profiles/default/bin:/run/current-system/sw/bin:/etc/profiles/per-user/cdenneen/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin";
      };
      KeepAlive = false;
      RunAtLoad = true;
      ProcessType = "Interactive";
      StandardOutPath = jarvisVoiceUiLogFile;
      StandardErrorPath = jarvisVoiceUiLogFile;
    };
  };
}
