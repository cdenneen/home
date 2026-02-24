{ pkgs }:
pkgs.writeShellScriptBin "update-workspace-agents" ''
    set -euo pipefail

    ${pkgs.nodejs_24}/bin/node <<'NODE'
    const fs = require("fs");
    const path = require("path");
    const http = require("http");

    const home = process.env.HOME || "/home/cdenneen";
    const workspaceRoot =
      process.env.WORKSPACE_ROOT ||
      (process.platform === "darwin"
        ? path.join(home, "code", "workspace")
        : path.join(home, "src", "workspace"));
    const limit = Number.parseInt(process.env.WORKSPACE_AGENT_LIMIT || "3", 10);
    const msgLimit = Number.parseInt(process.env.WORKSPACE_AGENT_MSG_LIMIT || "20", 10);
    const baseUrl = process.env.OPENCODE_API_URL || "http://127.0.0.1:4097";
    const prune = (process.env.WORKSPACE_AGENT_PRUNE || "1") !== "0";
    const createMissing = (process.env.WORKSPACE_AGENT_CREATE_MISSING || "0") === "1";

  function httpGetJson(url) {
    return new Promise((resolve, reject) => {
      http.get(url, (res) => {
        let data = "";
        res.on("data", (d) => (data += d));
        res.on("end", () => {
          if (res.statusCode && res.statusCode >= 400) {
            return reject(new Error("HTTP " + res.statusCode + ": " + data.slice(0, 200)));
          }
          try {
            resolve(JSON.parse(data));
          } catch (err) {
            reject(err);
          }
        });
      }).on("error", reject);
    });
  }

  function httpRequestJson(method, url, payload) {
    return new Promise((resolve, reject) => {
      const req = http.request(
        url,
        {
          method,
          headers: payload ? { "Content-Type": "application/json" } : {},
        },
        (res) => {
          let data = "";
          res.on("data", (d) => (data += d));
          res.on("end", () => {
            if (res.statusCode && res.statusCode >= 400) {
              return reject(new Error("HTTP " + res.statusCode + ": " + data.slice(0, 200)));
            }
            if (!data) return resolve(null);
            try {
              resolve(JSON.parse(data));
            } catch {
              resolve(null);
            }
          });
        }
      );
      req.on("error", reject);
      if (payload) req.write(JSON.stringify(payload));
      req.end();
    });
  }

    function textFromMessage(msg) {
      if (!msg || !Array.isArray(msg.parts)) return "";
      return msg.parts
        .filter((p) => p && p.type === "text" && typeof p.text === "string")
        .map((p) => p.text.trim())
        .filter(Boolean)
        .join("\n")
        .trim();
    }

    function truncate(text, max = 600) {
      if (!text) return "";
      if (text.length <= max) return text;
      return text.slice(0, max) + "…";
    }

    (async () => {
    const sessions = await httpGetJson(baseUrl + "/session");
      const groups = new Map();

    function workspaceFromTitle(title) {
      const m = /tg:\d+\s+([\w.-]+)/i.exec(title || "");
      if (m && m[1]) return m[1];
      const ws = /ws:([\w.-]+)/i.exec(title || "");
      if (ws && ws[1]) return ws[1];
      return "";
    }

    for (const s of sessions) {
      const dir = String(s.directory || s.workspace || "");
      const title = String(s.title || "");

      if (/subagent/i.test(title) || title.startsWith("Run ")) continue;

      let workspace = "";
      if (dir.startsWith(workspaceRoot + path.sep)) {
        const rel = path.relative(workspaceRoot, dir);
        workspace = rel.split(path.sep)[0];
      }

      if (!workspace) {
        workspace = workspaceFromTitle(title);
      }

      if (!workspace) continue;

      const updated = Number((s.time && s.time.updated) || 0);
      const entry = {
        id: String(s.id || ""),
        title,
        directory: dir,
        updated,
      };

      if (!groups.has(workspace)) groups.set(workspace, []);
      groups.get(workspace).push(entry);
    }

      const now = new Date();
      const stamp = now.toISOString().replace("T", " ").replace(/:\d+\.\d+Z$/, "Z");

      for (const [workspace, list] of groups.entries()) {
        list.sort((a, b) => b.updated - a.updated);
        const take = list.slice(0, Math.max(1, limit));
        const wsPath = path.join(workspaceRoot, workspace);
        const agentsPath = path.join(wsPath, "AGENTS.md");

        let out = "";
        out += "\n## " + stamp + "\n";

        for (const item of take) {
          const msgUrl = baseUrl + "/session/" + encodeURIComponent(item.id) + "/message";
          let messages = [];
          try {
            messages = await httpGetJson(msgUrl);
          } catch (err) {
            messages = [];
          }

          if (msgLimit > 0 && messages.length > msgLimit) {
            messages = messages.slice(-msgLimit);
          }

          let lastUser = "";
          let lastAssistant = "";
          for (let i = messages.length - 1; i >= 0; i -= 1) {
            if (!lastAssistant && messages[i]?.info?.role === "assistant") {
              lastAssistant = textFromMessage(messages[i]);
            }
            if (!lastUser && messages[i]?.info?.role === "user") {
              lastUser = textFromMessage(messages[i]);
            }
            if (lastUser && lastAssistant) break;
          }

          out += "- session " + item.id + " (" + (item.title || "untitled") + ")\n";
          if (lastUser) out += "- last_user: " + truncate(lastUser) + "\n";
          if (lastAssistant) out += "- last_assistant: " + truncate(lastAssistant) + "\n";
        }

        fs.mkdirSync(wsPath, { recursive: true });
        if (!fs.existsSync(agentsPath)) {
          fs.writeFileSync(agentsPath, `# Agent Guide (AGENTS.md)\n`);
        }
        fs.appendFileSync(agentsPath, out);
        process.stdout.write("Updated " + agentsPath + "\n");
      }

      if (!prune) return;

      const allSessions = await httpGetJson(baseUrl + "/session");
      const keepIds = new Set();

      for (const [workspace, list] of groups.entries()) {
        const wsPath = path.join(workspaceRoot, workspace);
      const candidates = allSessions
        .filter((s) => String(s.directory || "") === wsPath)
        .sort((a, b) => Number(b.time?.updated || 0) - Number(a.time?.updated || 0));

      const existing = candidates.find((s) => String(s.title || "") === "ws:" + workspace);
      if (existing) {
        if (String(existing.directory || "") !== wsPath) {
          try {
            await httpRequestJson("DELETE", baseUrl + "/session/" + encodeURIComponent(existing.id));
          } catch {
            // ignore delete failures
          }
        } else {
          keepIds.add(String(existing.id));
          continue;
        }
      }

      let picked = candidates[0];
      if (!picked && createMissing) {
        try {
          picked = await httpRequestJson("POST", baseUrl + "/session", {
            title: "ws:" + workspace,
            directory: wsPath,
          });
        } catch {
          picked = null;
        }
      }

      if (picked && picked.id) {
        keepIds.add(String(picked.id));
        try {
          await httpRequestJson("PATCH", baseUrl + "/session/" + encodeURIComponent(picked.id), {
            title: "ws:" + workspace,
          });
        } catch {
          // ignore rename failures
        }
      }
    }

      for (const s of allSessions) {
        const id = String(s.id || "");
        if (!id) continue;
        if (keepIds.has(id)) continue;
      try {
        await httpRequestJson("DELETE", baseUrl + "/session/" + encodeURIComponent(id));
      } catch {
        // ignore delete failures
      }
    }
    })().catch((err) => {
      console.error(err.stack || String(err));
      process.exit(1);
    });
  NODE
''
