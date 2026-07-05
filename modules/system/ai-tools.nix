{
  config,
  lib,
  pkgs,
  ...
}:
{
  options.profiles.aiTools.enable = lib.mkEnableOption "AI CLI tools (claude-code, opencode)";

  config = lib.mkIf config.profiles.aiTools.enable {
    environment.systemPackages = [
      pkgs.claude-code
      pkgs.codex
      pkgs.opencode
    ];
  };
}
