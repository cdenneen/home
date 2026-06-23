{
  config,
  jarvis,
  lib,
  pkgs,
  ...
}:
let
  jarvisSource = jarvis.outPath;
  jarvisSourceDirTemplate = "${jarvisBaseDir}/src.XXXXXX";
  primaryUser = config.system.primaryUser;
  homeDir = "/Users/${primaryUser}";
  jarvisBaseDir = "${homeDir}/.local/share/jarvis";
  jarvisDataDir = "${jarvisBaseDir}/data";
  jarvisRepoDir = "${jarvisBaseDir}/repos";
  jarvisVenvDir = "${jarvisBaseDir}/venv";
  jarvisPort = 8091;
  jarvisEnvFile = "${homeDir}/Library/Application Support/jarvis/work-runner.env";
  jarvisResourcePackage = pkgs.runCommand "jarvis-resources" { } ''
    mkdir -p "$out"
    cp -R ${jarvis.outPath}/config "$out/config"
    cp -R ${jarvis.outPath}/context "$out/context"
    cp -R ${jarvis.outPath}/web "$out/web"
    cp -R ${jarvis.outPath}/schemas "$out/schemas"
    cp -R ${jarvis.outPath}/scripts "$out/scripts"
  '';
  jarvisBootstrap = pkgs.writeShellScript "jarvis-node-bootstrap" ''
    set -euo pipefail
    venv="${jarvisVenvDir}"
    if [ ! -x "$venv/bin/python" ]; then
      ${pkgs.python3}/bin/python3 -m venv "$venv"
    fi
    if [ ! -x "$venv/bin/jarvis-node-service" ]; then
      "$venv/bin/python" -m pip install --upgrade pip
      src_dir="$(${pkgs.coreutils}/bin/mktemp -d "${jarvisSourceDirTemplate}")"
      ${pkgs.coreutils}/bin/cp -R ${jarvisSource}/. "$src_dir"
      ${pkgs.coreutils}/bin/chmod -R u+rwX "$src_dir"
      "$venv/bin/python" -m pip uninstall -y jarvis || true
      "$venv/bin/python" -m pip install "$src_dir"
    fi
  '';
  jarvisScript = pkgs.writeShellScript "jarvis-node-launchd" ''
    set -euo pipefail
    ${pkgs.coreutils}/bin/mkdir -p "${jarvisDataDir}" "${jarvisRepoDir}"
    ${jarvisBootstrap}
    if [ -f "${jarvisEnvFile}" ]; then
      set -a
      . "${jarvisEnvFile}"
      set +a
    fi
    export JARVIS_RESOURCE_ROOT="${jarvisResourcePackage}"
    export JARVIS_DATA_DIR="${jarvisDataDir}"
    exec "${jarvisVenvDir}/bin/jarvis-node-service" \
      --host 0.0.0.0 \
      --port ${toString jarvisPort} \
      --repo-dir "${jarvisRepoDir}"
  '';
in
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

    launchd.agents.jarvis-node = {
      enable = true;
      config = {
        ProgramArguments = [ "${jarvisScript}" ];
        EnvironmentVariables = {
          HOME = homeDir;
        };
        KeepAlive = true;
        RunAtLoad = true;
        ProcessType = "Background";
        StandardOutPath = "${homeDir}/Library/Logs/jarvis-node.log";
        StandardErrorPath = "${homeDir}/Library/Logs/jarvis-node.log";
      };
    };

  };

  environment.systemPackages = [
    pkgs.bash
    pkgs.nodejs_24
    pkgs.pnpm
    pkgs.podman
    pkgs.uv
  ];

}
