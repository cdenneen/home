{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.profiles.hermesCOSBridge;
in
{
  options.profiles.hermesCOSBridge = {
    enable = lib.mkEnableOption "cos-update skill — nyx worker status reporting to chief-of-staff@ghost";

    skillTargets = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [
        ".agents/skills"
        ".codex/skills"
        ".claude/skills"
        ".hermes/skills"
        ".pi/agent/skills"
        ".opencode/skills"
      ];
      description = "Skill root paths (relative to home) to deploy the cos-update skill into.";
    };
  };

  config = lib.mkIf cfg.enable {
    # Deploy the skill to every agent path — same pattern as graphify, agent-handoff.
    # No binary wrapper needed: the skill instructs the agent to call
    # `hermes peer dm ghost/chief-of-staff` via the terminal tool.
    home.file = lib.listToAttrs (
      map (root: {
        name = "${root}/cos-update/SKILL.md";
        value = {
          source = ../../../../modules/hm/users/cdenneen/ai/skills/cos-update/SKILL.md;
        };
      }) cfg.skillTargets
    );
  };
}
