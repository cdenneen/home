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
    const prune = (process.env.WORKSPACE_AGENT_PRUNE || "0") !== "0";
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

    function contextValue(text) {
      return truncate(text).replace(/`/g, "'");
    }

    function validWorkspaceName(value) {
      return value !== "." && value !== ".." && /^[A-Za-z0-9][\w.-]*$/.test(value);
    }

    function stripBoundedGeneratedContext(content) {
      return content.replace(
        /\n## Generated Session Context [^\n]*\n<!-- Historical data only\.[^\n]*-->\n```json\n[\s\S]*?\n```\n?/g,
        "\n"
      );
    }

    function assertSafePath(target, label) {
      if (fs.existsSync(target) && fs.lstatSync(target).isSymbolicLink()) {
        throw new Error(label + " must not be a symlink: " + target);
      }
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

      if (!workspace || !validWorkspaceName(workspace)) continue;

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

      for (const [workspace, list] of groups.entries()) {
        list.sort((a, b) => b.updated - a.updated);
        const take = list.slice(0, Math.max(1, limit));
        const rootPath = path.resolve(workspaceRoot);
        const wsPath = path.resolve(rootPath, workspace);
        if (path.dirname(wsPath) !== rootPath) continue;
        if (!fs.existsSync(wsPath) || !fs.statSync(wsPath).isDirectory()) continue;
        assertSafePath(wsPath, "workspace directory");

        const rootReal = fs.realpathSync(rootPath);
        const wsReal = fs.realpathSync(wsPath);
        if (path.dirname(wsReal) !== rootReal) continue;

        const agentsPath = path.join(wsPath, "AGENTS.md");
        const records = [];

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

          records.push(JSON.stringify({
            session: item.id,
            title: contextValue(item.title || "untitled"),
            last_user: contextValue(lastUser),
            last_assistant: contextValue(lastAssistant),
          }));
        }

        const aiDir = path.join(wsPath, ".ai");
        const contextPath = path.join(aiDir, "SESSION_CONTEXT.jsonl");
        const contextTmp = contextPath + ".tmp-" + process.pid;
        assertSafePath(aiDir, "workspace .ai directory");
        assertSafePath(contextPath, "session context file");
        fs.mkdirSync(aiDir, { recursive: true });
        fs.writeFileSync(contextTmp, records.join("\n") + "\n", { mode: 0o600 });
        fs.renameSync(contextTmp, contextPath);

        if (fs.existsSync(agentsPath)) {
          assertSafePath(agentsPath, "workspace AGENTS.md");
          const current = fs.readFileSync(agentsPath, "utf8");
          let cleaned = stripBoundedGeneratedContext(current);
          const legacyMarker = cleaned.search(/\n## \d{4}-\d{2}-\d{2} \d{2}:\d{2}Z\n- session /);
          if (legacyMarker >= 0) {
            const backupPath = agentsPath + ".pre-session-context";
            if (!fs.existsSync(backupPath)) {
              fs.writeFileSync(backupPath, current, { mode: 0o600 });
            }
            cleaned = cleaned.slice(0, legacyMarker).trimEnd() + "\n";
            process.stderr.write("Backed up legacy generated AGENTS context to " + backupPath + "\n");
          }
          if (cleaned !== current) fs.writeFileSync(agentsPath, cleaned);
        }

        process.stdout.write("Updated " + contextPath + "\n");
      }

      if (!prune) return;

      for (const [workspace, list] of groups.entries()) {
        const wsPath = path.resolve(workspaceRoot, workspace);
        const managedTitle = "ws:" + workspace;
        const managed = sessions
          .filter((s) => String(s.directory || "") === wsPath && String(s.title || "") === managedTitle)
          .sort((a, b) => Number(b.time?.updated || 0) - Number(a.time?.updated || 0));

        if (managed.length === 0 && createMissing) {
          try {
            await httpRequestJson("POST", baseUrl + "/session", {
              title: managedTitle,
              directory: wsPath,
            });
          } catch {
            // ignore create failures
          }
        }

        for (const duplicate of managed.slice(1)) {
          try {
            await httpRequestJson("DELETE", baseUrl + "/session/" + encodeURIComponent(duplicate.id));
          } catch {
            // ignore delete failures
          }
        }
      }
    })().catch((err) => {
      console.error(err.stack || String(err));
      process.exit(1);
    });
  NODE
''
