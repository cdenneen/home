{
  agentPkgs ? null,
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.profiles.hermesAssistant;
  profileHome = "${config.home.homeDirectory}/.hermes/profiles/assistant";
  sopsNixProgram = config.systemd.user.services.sops-nix.Service.ExecStart;
  assistantPython = pkgs.python3.withPackages (pythonPackages: [
    pythonPackages.msal
    pythonPackages.requests
  ]);
  googleWorkspace = pkgs.writeShellScriptBin "hermes-google-workspace" ''
    export HERMES_HOME=${lib.escapeShellArg profileHome}
    exec ${agentPkgs.hermes.hermesVenv}/bin/python3 \
      ${agentPkgs.hermes}/share/hermes-agent/skills/productivity/google-workspace/scripts/google_api.py "$@"
  '';
  microsoftGraph = pkgs.writeShellScriptBin "hermes-msgraph" ''
    export HERMES_MSGRAPH_TOKEN_CACHE=${lib.escapeShellArg "${profileHome}/msgraph_token_cache.json"}
    exec ${assistantPython}/bin/python3 ${./msgraph.py} "$@"
  '';
in
{
  options.profiles.hermesAssistant = {
    personal.enable = lib.mkEnableOption "the personal Google assistant profile on Ghost";
    work.enable = lib.mkEnableOption "the work Microsoft Graph assistant profile on Nyx";
  };

  config = lib.mkIf (cfg.personal.enable || cfg.work.enable) {
    assertions = [
      {
        assertion = pkgs.stdenv.hostPlatform.isLinux && agentPkgs != null && agentPkgs ? hermes;
        message = "profiles.hermesAssistant requires Linux and agentPkgs.hermes";
      }
      {
        assertion = !(cfg.personal.enable && cfg.work.enable);
        message = "A Hermes assistant host cannot hold both personal and work OAuth credentials";
      }
    ];

    home.packages =
      lib.optionals cfg.personal.enable [ googleWorkspace ]
      ++ lib.optionals cfg.work.enable [ microsoftGraph ];

    home.file = lib.mkMerge [
      (lib.mkIf cfg.personal.enable {
        ".hermes/profiles/assistant/skills/productivity/personal-google-assistant/SKILL.md".source =
          ./personal-google-assistant.md;
      })
      (lib.mkIf cfg.work.enable {
        ".hermes/profiles/assistant/skills/productivity/work-microsoft-assistant/SKILL.md".source =
          ./work-microsoft-assistant.md;
      })
    ];

    home.activation.hermesAssistantOauth =
      lib.hm.dag.entryAfter
        [
          "hermesProfileModelConfig"
          "materializeLinuxSopsSecrets"
        ]
        ''
              set -euo pipefail

          seed_oauth() {
            source_path="$1"
            target_path="$2"

            if [ ! -r "$source_path" ]; then
              $DRY_RUN_CMD ${lib.escapeShellArg sopsNixProgram}
            fi
            if [ ! -r "$source_path" ]; then
                  echo "error: Hermes assistant OAuth seed is missing: $source_path" >&2
                  exit 1
                fi

                $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -d -m 700 "$(${pkgs.coreutils}/bin/dirname "$target_path")"
                if [ ! -e "$target_path" ]; then
                  $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 600 -T "$source_path" "$target_path"
                elif [ ! -f "$target_path" ]; then
                  echo "error: Hermes assistant OAuth target is not a regular file: $target_path" >&2
                  exit 1
                else
                  $DRY_RUN_CMD ${pkgs.coreutils}/bin/chmod 600 "$target_path"
                fi
              }

              ${lib.optionalString cfg.personal.enable ''
                seed_oauth \
                  ${lib.escapeShellArg config.sops.secrets.hermes_assistant_google_oauth_ghost.path} \
                  ${lib.escapeShellArg "${profileHome}/google_token.json"}
              ''}
              ${lib.optionalString cfg.work.enable ''
                seed_oauth \
                  ${lib.escapeShellArg config.sops.secrets.hermes_assistant_msgraph_oauth_nyx.path} \
                  ${lib.escapeShellArg "${profileHome}/msgraph_token_cache.json"}
              ''}
        '';
  };
}
