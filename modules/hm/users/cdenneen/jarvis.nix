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
  jarvisStateFile = "${jarvisRuntimeDir}/voice-edge-state.json";
  jarvisLogFile = "${homeDir}/Library/Logs/jarvis-voice-edge.log";
  jarvisPython = pkgs.python3.withPackages (
    ps: with ps; [
      websockets
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
}
