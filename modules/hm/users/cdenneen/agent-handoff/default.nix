{
  config,
  lib,
  pkgs,
  agentPkgs,
  ...
}:
let
  cfg = config.profiles.agentHandoff;

  # Standalone script — proper executable in the Nix store, works in any
  # POSIX shell (sh/bash/zsh) including Claude Code's subshell.
  agentResumeScript = pkgs.writeScriptBin "agent-resume" ''
    #!${pkgs.python3}/bin/python3
    import sys, urllib.request, json, os

    if len(sys.argv) < 2:
        print("usage: agent-resume <handoff-id>", file=sys.stderr)
        sys.exit(1)

    handoff_id = sys.argv[1]
    # LiteLLM aggregate at :4000 — port 18123 is localhost-only on eros.
    # EROS_LITELLM_API_KEY is exported by secrets.nix on every host.
    api_key = os.environ.get("EROS_LITELLM_API_KEY", "")
    url = "http://eros.tail0e55.ts.net:4000/mcp"

    def mcp_post(method, params, sid=None, id_=1, timeout=15):
        hdrs = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if api_key:
            hdrs["Authorization"] = "Bearer " + api_key
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
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.headers.get("mcp-session-id", ""), resp.read().decode()

    # 1. initialize — get session id
    sid, _ = mcp_post(
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "agent-resume", "version": "1"},
        },
    )

    # 2. notifications/initialized — fire-and-forget, short timeout, ignore errors
    try:
        mcp_post("notifications/initialized", {}, sid, timeout=3)
    except Exception:
        pass

    # 3. call get_resume_prompt via agent_handoff- prefixed name (aggregate namespace)
    _, body = mcp_post(
        "tools/call",
        {"name": "agent_handoff-get_resume_prompt", "arguments": {"handoff_id": handoff_id}},
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
