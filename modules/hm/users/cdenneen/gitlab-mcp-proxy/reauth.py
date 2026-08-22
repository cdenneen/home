#!/usr/bin/env python3
"""
Bootstrap or refresh creds.json for gitlab-mcp-proxy via GitLab's OAuth
authorization code flow.

Run this:
  - Once on a brand-new host, after `home-manager switch` has deployed the
    gitlab-mcp-proxy module (client_id/client_secret come from sops - this
    script only needs to complete the interactive browser-authorization step
    and capture the resulting code).
  - Any time the refresh_token in creds.json stops working (OAuth app
    deleted/recreated, refresh_token revoked or rotated out from under this
    proxy by another client sharing the same client_id).

client_id/client_secret are supplied via GITLAB_MCP_CLIENT_ID_FILE and
GITLAB_MCP_CLIENT_SECRET_FILE (sops-nix decrypted secret paths) so this
script never needs them typed or pasted manually - only the one-time OAuth
authorization code.
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

GITLAB_URL = "https://git.ap.org"
REDIRECT_URI = "http://localhost:8765/callback"
SCOPE = "mcp"


def read_secret_file(env_var):
    path = os.environ.get(env_var)
    if not path or not os.path.isfile(path):
        sys.exit(
            f"error: {env_var} is not set or does not point to a readable file "
            f"(got: {path!r}). This script expects to run via the "
            f"gitlab-mcp-proxy-reauth wrapper, which sets this from the sops-nix "
            f"secret path."
        )
    with open(path) as f:
        return f.read().strip()


def build_authorize_url(client_id):
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "state": "gitlab-mcp-proxy-reauth",
    }
    return f"{GITLAB_URL}/oauth/authorize?" + urllib.parse.urlencode(params)


def extract_code(pasted):
    """Accept either a bare code or the full redirected callback URL."""
    pasted = pasted.strip()
    if pasted.startswith("http://") or pasted.startswith("https://"):
        query = urllib.parse.urlparse(pasted).query
        code = urllib.parse.parse_qs(query).get("code", [None])[0]
        if not code:
            sys.exit("error: no 'code' parameter found in the pasted URL")
        return code
    return pasted


def exchange_code(client_id, client_secret, code):
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    }
    req = urllib.request.Request(
        f"{GITLAB_URL}/oauth/token",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--code",
        help="Authorization code (or full redirected callback URL) if you already have it, "
        "to skip the interactive prompt.",
    )
    ap.add_argument(
        "--creds-path",
        default=os.path.expanduser("~/.hermes/gitlab_mcp_proxy/creds.json"),
    )
    args = ap.parse_args()

    client_id = read_secret_file("GITLAB_MCP_CLIENT_ID_FILE")
    client_secret = read_secret_file("GITLAB_MCP_CLIENT_SECRET_FILE")

    code = args.code
    if not code:
        url = build_authorize_url(client_id)
        print("Open this URL in a browser, approve the request, then paste the")
        print("resulting redirect URL (or just the 'code=' value) back here:")
        print()
        print(url)
        print()
        pasted = input("Paste redirect URL or code: ")
        code = extract_code(pasted)
    else:
        code = extract_code(code)

    tok = exchange_code(client_id, client_secret, code)

    creds = {
        "gitlab_url": GITLAB_URL,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT_URI,
        "access_token": tok["access_token"],
        "refresh_token": tok["refresh_token"],
        "expires_at": int(time.time()) + int(tok.get("expires_in", 7200)) - 120,
    }

    os.makedirs(os.path.dirname(args.creds_path), exist_ok=True)
    os.chmod(os.path.dirname(args.creds_path), 0o700)
    tmp = args.creds_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(creds, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, args.creds_path)

    print(f"wrote {args.creds_path} (mode 0600) - restart gitlab-mcp-proxy.service to pick it up:")
    print("  systemctl --user restart gitlab-mcp-proxy.service")


if __name__ == "__main__":
    main()
