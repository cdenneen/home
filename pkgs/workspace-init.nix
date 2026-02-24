{ pkgs }:
pkgs.writeShellScriptBin "workspace-init" ''
    set -euo pipefail

    name="${"1:-"}"
    if [ -z "$name" ]; then
      echo "Usage: workspace-init <name>" >&2
      exit 2
    fi

    ${pkgs.nodejs_24}/bin/node <<'NODE'
    const fs = require("fs");
    const path = require("path");
    const http = require("http");

    const name = process.argv[1];
    const home = process.env.HOME || "/home/cdenneen";
    const workspaceRoot =
      process.env.WORKSPACE_ROOT ||
      (process.platform === "darwin"
        ? path.join(home, "code", "workspace")
        : path.join(home, "src", "workspace"));
    const baseUrl = process.env.OPENCODE_API_URL || "http://127.0.0.1:4097";

    if (!name) {
      console.error("workspace-init: missing workspace name");
      process.exit(2);
    }

    const wsPath = path.join(workspaceRoot, name);
    fs.mkdirSync(wsPath, { recursive: true });

    const agentsPath = path.join(wsPath, "AGENTS.md");
    if (!fs.existsSync(agentsPath)) {
      fs.writeFileSync(agentsPath, "# Agent Guide (AGENTS.md)\n");
    }

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

    (async () => {
      const sessions = await httpGetJson(baseUrl + "/session");
      const title = "ws:" + name;
      const existing = sessions.find(
        (s) => String(s.title || "") === title && String(s.directory || s.workspace || "") === wsPath
      );
      if (existing) {
        console.log("Workspace session already exists: " + existing.id);
        return;
      }

      const created = await httpRequestJson("POST", baseUrl + "/session", {
        title,
        directory: wsPath,
      });

      if (!created || !created.id) {
        console.error("workspace-init: failed to create session");
        process.exit(1);
      }

      const check = await httpGetJson(baseUrl + "/session/" + encodeURIComponent(created.id));
      const dir = String(check.directory || "");
      if (dir !== wsPath) {
        console.error("workspace-init: session directory mismatch, deleting session");
        await httpRequestJson("DELETE", baseUrl + "/session/" + encodeURIComponent(created.id));
        process.exit(1);
      }

      console.log("Created workspace session: " + created.id);
    })().catch((err) => {
      console.error(err.stack || String(err));
      process.exit(1);
    });
  NODE
''
