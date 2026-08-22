#!/usr/bin/env python3
"""
Tiny local reverse proxy that fronts GitLab's native MCP server
(https://<gitlab>/api/v4/mcp) and transparently keeps a shared OAuth
access token refreshed, since GitLab MCP access tokens are short-lived
(2h) and Hermes's native MCP client only supports static headers with
no refresh logic.

Hermes points its mcp_servers.<name>.url at http://127.0.0.1:<port>/mcp
with no Authorization header of its own -- this proxy injects a fresh
Bearer token on every forwarded request, refreshing via the OAuth
refresh_token grant whenever the cached access token is near expiry.

Credentials (client_id/secret/access_token/refresh_token/expires_at)
live in creds.json next to this script, mode 0600. This file is
rewritten in place whenever a refresh happens, so restarting the proxy
picks up the latest token automatically.

Usage:
    python3 proxy.py [--port 8899]

Run under `terminal(background=True)` as a long-lived process, or
wire into a systemd/launchd unit if you want it to survive reboots.
"""
import argparse
import json
import os
import sys
import threading
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CREDS_PATH = os.path.join(HERE, "creds.json")
LOCK = threading.Lock()


def load_creds():
    with open(CREDS_PATH) as f:
        return json.load(f)


def save_creds(creds):
    tmp = CREDS_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(creds, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, CREDS_PATH)


def refresh_access_token(creds):
    """POST the refresh_token grant to GitLab's OAuth token endpoint."""
    payload = {
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": creds["refresh_token"],
        "grant_type": "refresh_token",
        "redirect_uri": creds["redirect_uri"],
    }
    url = creds["gitlab_url"].rstrip("/") + "/oauth/token"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        tok = json.loads(resp.read().decode())
    creds["access_token"] = tok["access_token"]
    # GitLab rotates the refresh token on each use -- always store the new one
    creds["refresh_token"] = tok.get("refresh_token", creds["refresh_token"])
    creds["expires_at"] = int(time.time()) + int(tok.get("expires_in", 7200)) - 120
    save_creds(creds)
    return creds


def get_valid_access_token():
    with LOCK:
        creds = load_creds()
        if time.time() >= creds.get("expires_at", 0):
            creds = refresh_access_token(creds)
        return creds["access_token"]


class MCPProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.client_address[0], fmt % args))

    def _forward(self):
        try:
            creds = load_creds()
            gitlab_url = creds["gitlab_url"].rstrip("/") + "/api/v4/mcp"
            token = get_valid_access_token()

            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""

            fwd_headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": self.headers.get("Content-Type", "application/json"),
                "Accept": self.headers.get("Accept", "application/json, text/event-stream"),
            }

            req = urllib.request.Request(gitlab_url, data=body, headers=fwd_headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    status = resp.status
                    resp_body = resp.read()
                    resp_headers = dict(resp.headers)
            except urllib.error.HTTPError as e:
                # Retry once on 401 in case the token was revoked/rotated out of band
                if e.code == 401:
                    with LOCK:
                        creds = load_creds()
                        creds = refresh_access_token(creds)
                    fwd_headers["Authorization"] = f"Bearer {creds['access_token']}"
                    req = urllib.request.Request(gitlab_url, data=body, headers=fwd_headers, method="POST")
                    with urllib.request.urlopen(req, timeout=120) as resp:
                        status = resp.status
                        resp_body = resp.read()
                        resp_headers = dict(resp.headers)
                else:
                    status = e.code
                    resp_body = e.read()
                    resp_headers = dict(e.headers or {})

            self.send_response(status)
            for k, v in resp_headers.items():
                if k.lower() in ("content-length", "transfer-encoding", "connection"):
                    continue
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)
        except Exception as exc:
            msg = json.dumps({"error": str(exc)}).encode()
            try:
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
            except Exception:
                pass

    def do_POST(self):
        self._forward()

    def do_GET(self):
        if self.path in ("/health", "/healthz"):
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._forward()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--bind", default="127.0.0.1")
    args = ap.parse_args()

    # Fail fast if creds.json is missing/invalid
    load_creds()

    server = ThreadingHTTPServer((args.bind, args.port), MCPProxyHandler)
    print(f"gitlab-mcp-proxy listening on http://{args.bind}:{args.port}/mcp", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
