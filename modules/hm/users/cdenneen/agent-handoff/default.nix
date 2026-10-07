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
          # Fetch resume prompt via MCP SSE protocol (agent-handoff server on eros:18123).
          ${pkgs.python3}/bin/python3 - "''${id}" <<'PYEOF'
import sys, urllib.request, json
handoff_id = sys.argv[1]
url = 'http://eros.tail0e55.ts.net:18123/mcp'
def mcp(method, params, sid=None, id_=1):
    hdrs = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}
    if sid: hdrs['mcp-session-id'] = sid
    r = urllib.request.Request(url, method='POST', headers=hdrs,
        data=json.dumps({'jsonrpc':'2.0','method':method,'params':params,'id':id_}).encode())
    with urllib.request.urlopen(r, timeout=15) as resp:
        return resp.headers.get('mcp-session-id',''), resp.read().decode()
sid, _ = mcp('initialize', {'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'agent-resume','version':'1'}})
mcp('notifications/initialized', {}, sid)
_, body = mcp('tools/call', {'name':'get_resume_prompt','arguments':{'handoff_id':handoff_id}}, sid, 2)
for line in body.split('\n'):
    if line.startswith('data:'):
        try:
            d = json.loads(line[5:])
            if 'result' in d:
                text = json.loads(d['result']['content'][0]['text'])
                print(text.get('prompt', text.get('error', 'handoff not found')))
                sys.exit(0)
        except: pass
print('handoff not found', file=sys.stderr); sys.exit(1)
PYEOF
        }; _agent_resume
      '';
    };
  };
}
