{
  config,
  lib,
  pkgs,
  agentPkgs,
  ...
}:
let
  cfg = config.profiles.agentHandoff;
in
{
  options.profiles.agentHandoff = {
    enable = lib.mkEnableOption "agent-handoff service and skill deployment";

    skillTargets = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [
        ".agents/skills"
        ".opencode/skills"
        ".codex/skills"
        ".claude/skills"
        ".hermes/skills"
        ".pi/agent/skills"
      ];
      description = "Skill root paths (relative to home) to deploy the agent-handoff skill into.";
    };
  };

  config = lib.mkIf cfg.enable {
    # Deploy the skill to every agent that has a skills directory.
    # Same pattern as graphify and gitlab-pipelines in files.nix.
    home.file = lib.listToAttrs (
      map (root: {
        name = "${root}/agent-handoff/SKILL.md";
        value = {
          source = ./SKILL.md;
        };
      }) cfg.skillTargets
    );

    # agent-resume shell alias: fetches a handoff prompt from eros and prints
    # it so users can pipe it directly to any agent CLI.
    # Usage: claude "$(agent-resume <id>)"
    programs.zsh.shellAliases = {
      agent-resume = ''
        _agent_resume() {
          local id="''${1:?usage: agent-resume <handoff-id>}"
          # Call the agent-handoff MCP tool via the eros HTTP endpoint.
          # Requires EROS_LITELLM_API_KEY (already in shell env via secrets.nix).
          curl -sf \
            -H "Authorization: Bearer ''${EROS_LITELLM_API_KEY}" \
            -H "x-eros-consumer: ''${HOSTNAME:-local}" \
            -H "x-eros-trust-domain: ''${EROS_TRUST_DOMAIN:-work}" \
            -H "x-eros-workload: agent-resume-cli" \
            "http://eros.tail0e55.ts.net:18123/mcp/tools/get_resume_prompt" \
            --json "{\"handoff_id\":\"''${id}\"}" \
          | ${pkgs.jq}/bin/jq -r '.prompt // .error // "handoff not found"'
        }; _agent_resume
      '';
    };
  };
}
