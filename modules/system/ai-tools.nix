{
  config,
  lib,
  unstablePkgs ? null,
  pkgs,
  ...
}:
let
  aiPkgs = if unstablePkgs != null then unstablePkgs else pkgs;
in
{
  options.profiles.aiTools.enable = lib.mkEnableOption "AI CLI tools (claude-code, opencode)";

  config = lib.mkIf config.profiles.aiTools.enable {
    environment.systemPackages = [
      aiPkgs.claude-code
      aiPkgs.codex
      aiPkgs.opencode
    ];
  };
}
