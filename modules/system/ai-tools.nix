{
  config,
  agentPkgs ? null,
  lib,
  unstablePkgs ? null,
  pkgs,
  ...
}:
let
  aiPkgs =
    if agentPkgs != null then
      agentPkgs
    else if unstablePkgs != null then
      unstablePkgs
    else
      pkgs;
in
{
  options.profiles.aiTools.enable = lib.mkEnableOption "AI CLI tools (claude-code, codex, opencode, pi)";

  config = lib.mkIf config.profiles.aiTools.enable {
    environment.systemPackages = [
      aiPkgs.claude-code
      aiPkgs.codex
      aiPkgs.opencode
      aiPkgs.pi
      pkgs.nodejs_24
    ];
  };
}
