{
  config,
  lib,
  pkgs,
  agentPkgs,
  ...
}:
let
  cfg = config.profiles.agentHandoff;

  # Standalone script — avoids heredoc syntax issues inside Nix strings and
  # works in any POSIX shell (sh/bash/zsh), including Claude Code's subshell.
  agentResumeScript = pkgs.writeScript "agent-resume" ''
    #!${pkgs.python3}/bin/python3
    import sys, urllib.request, json

    if len(sys.argv) < 2:
        print("usage: agent-resume <handoff-id>", file=sys.stderr)
        sys.exit(1)

    handoff_id = sys.argv[1]
    url = "http://eros.tail0e55.ts.net:18123/mcp"

    def mcp_call(method, params, sid=None, id_=1):
        hdrs = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if sid:
            hdrs["mcp-session-id"] = sid
        req = urllib.request.Request(
            url,
            method="POST",
            headers=hdrs,
            data=json.dumps(
                {"jsonrpc": "2.0", "method": method, "params": params, "id": id_}
            ).encode(),
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.headers.get("mcp-session-id", ""), resp.read().decode()

    sid, _ = mcp_call(
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "agent-resume", "version": "1"},
        },
    )
    mcp_call("notifications/initialized", {}, sid)
    _, body = mcp_call(
        "tools/call",
        {"name": "get_resume_prompt", "arguments": {"handoff_id": handoff_id}},
        sid,
        2,
    )
    for line in body.split("\n"):
        if line.startswith("data:"):
            try:
                d = json.loads(line[5:])
                if "result" in d:
                    text = json.loads(d["result"]["content"][0]["text"])
                    print(text.get("prompt", text.get("error", "handoff not found")))
                    sys.exit(0)
            except Exception:
                pass
    print("handoff not found", file=sys.stderr)
    sys.exit(1)
  '';
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
    home.file = lib.listToAttrs (
      map (root: {
        name = "${root}/agent-handoff/SKILL.md";
        value = {
          source = ./SKILL.md;
        };
      }) cfg.skillTargets
    );

    # agent-resume: fetch a handoff's resume prompt from eros.
    # Works in any shell including Claude Code's sh subshell.
    # Usage: claude "$(agent-resume <id>)"  or  pi "$(agent-resume <id>)"
    home.packages = [ agentResumeScript ];
  };
}
