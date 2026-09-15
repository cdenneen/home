"""Run an isolated LiteLLM 1.94.0 integration fixture on Eros."""

from __future__ import annotations

import argparse
import http.client
import json
import secrets
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PINNED_IMAGE = (
    "ghcr.io/berriai/litellm@"
    "sha256:65d84a2282137b4dc73bbe184650a7c807177c533e4223b3bfbc87963fe3fabe"
)
VIRTUAL_TOOLS = {"mcp_tool_call", "mcp_tool_search"}


def run(
    command: list[str], *, input_text: str = "", check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        check=False,
        input=input_text or None,
        text=True,
        capture_output=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(command)}\n{result.stdout}\n{result.stderr}"
        )
    return result


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def parse_response(payload: bytes, content_type: str) -> Any:
    text = payload.decode(errors="replace")
    if not text.strip():
        return {}
    if "text/event-stream" not in content_type and not text.lstrip().startswith(
        "data:"
    ):
        return json.loads(text)
    events = [
        json.loads(line[5:].strip())
        for line in text.splitlines()
        if line.startswith("data:")
    ]
    return events[-1] if events else {}


def request_json(
    url: str,
    payload: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30,
) -> tuple[int, Any, dict[str, str]]:
    request_headers = {"Accept": "application/json, text/event-stream"}
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    request_headers.update(headers or {})
    request = urllib.request.Request(
        url,
        data=None if payload is None else json.dumps(payload).encode(),
        headers=request_headers,
        method="GET" if payload is None else "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            return (
                response.status,
                parse_response(body, response.headers.get("Content-Type", "")),
                {key.lower(): value for key, value in response.headers.items()},
            )
    except urllib.error.HTTPError as error:
        return (
            error.code,
            parse_response(error.read(), error.headers.get("Content-Type", "")),
            {key.lower(): value for key, value in error.headers.items()},
        )
    except urllib.error.URLError as error:
        return 0, {"error": str(error.reason)}, {}


class FixtureMcpHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length) or b"{}")
        method = request.get("method")
        if method == "notifications/initialized":
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if method == "initialize":
            result = {
                "protocolVersion": request.get("params", {}).get(
                    "protocolVersion", "2025-06-18"
                ),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "eros-integration-fixture", "version": "1"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "fixture_echo",
                        "description": "Echo integration fixture text",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"],
                        },
                    }
                ]
            }
        elif method == "tools/call":
            text = request.get("params", {}).get("arguments", {}).get("text", "")
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        else:
            result = {}
        response = json.dumps(
            {"jsonrpc": "2.0", "id": request.get("id"), "result": result}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header(
            "Mcp-Session-Id", self.headers.get("Mcp-Session-Id", "fixture-session")
        )
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


class Fixture:
    def __init__(self, policy_path: Path, disable_budget_reservation: bool) -> None:
        suffix = uuid.uuid4().hex[:10]
        self.database = f"eros_it_{suffix}"
        self.role = f"eros_it_{suffix}"
        self.password = secrets.token_hex(16)
        self.master_key = f"sk-{secrets.token_urlsafe(24)}"
        self.container = f"eros-litellm-it-{suffix}"
        self.proxy_port = free_port()
        self.mcp_port = free_port()
        self.policy_path = policy_path.resolve()
        self.disable_budget_reservation = disable_budget_reservation
        self.tempdir = tempfile.TemporaryDirectory(prefix="eros-litellm-it-")
        self.mock_server = ThreadingHTTPServer(
            ("127.0.0.1", self.mcp_port), FixtureMcpHandler
        )
        self.mock_thread = threading.Thread(
            target=self.mock_server.serve_forever, daemon=True
        )
        self.database_created = False
        self.context_role_created = False
        self.mock_started = False

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.proxy_port}"

    def psql(self, sql: str, database: str = "postgres") -> str:
        result = run(
            [
                "sudo",
                "-n",
                "-u",
                "postgres",
                "psql",
                "--dbname",
                database,
                "--no-psqlrc",
                "--tuples-only",
                "--no-align",
                "--set",
                "ON_ERROR_STOP=1",
                "--command",
                sql,
            ]
        )
        return result.stdout.strip()

    def start(self) -> None:
        run(["sudo", "-n", "true"])
        if self.psql("SELECT 1 FROM pg_roles WHERE rolname = 'eros_context'") != "1":
            self.psql("CREATE ROLE eros_context NOLOGIN")
            self.context_role_created = True
        self.psql(f"CREATE ROLE {self.role} LOGIN PASSWORD '{self.password}'")
        self.psql(
            f"CREATE DATABASE {self.database} OWNER {self.role} "
            "TEMPLATE template0 LC_COLLATE 'C' LC_CTYPE 'C'"
        )
        self.database_created = True
        self.mock_thread.start()
        self.mock_started = True
        directory = Path(self.tempdir.name)
        config = directory / "config.yaml"
        environment = directory / "env"
        config.write_text(
            f"""model_list:
  - model_name: claude-sonnet-4-6
    litellm_params:
      model: bedrock/us.anthropic.claude-sonnet-4-6
      aws_region_name: us-east-1
      cache_control_injection_points:
        - location: tool_config
          control:
            type: ephemeral
        - location: message
          role: system
          control:
            type: ephemeral
        - location: message
          index: -1
          control:
            type: ephemeral
general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  database_url: os.environ/DATABASE_URL
  disable_budget_reservation: {str(self.disable_budget_reservation).lower()}
litellm_settings:
  cache: false
  enable_anthropic_prompt_caching: true
  extra_spend_tag_headers:
    - x-eros-consumer
    - x-eros-trust-domain
    - x-eros-workload
    - x-eros-session
router_settings:
  cache_responses: false
mcp_servers:
  fixture:
    server_id: fixture
    url: http://127.0.0.1:{self.mcp_port}/mcp
    transport: http
    description: Disposable integration fixture
""",
            encoding="utf-8",
        )
        config.chmod(0o644)
        environment.write_text(
            "\n".join(
                [
                    f"LITELLM_MASTER_KEY={self.master_key}",
                    f"DATABASE_URL=postgresql://{self.role}:{self.password}@127.0.0.1:5432/{self.database}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        environment.chmod(0o600)
        run(
            [
                "sudo",
                "-n",
                "podman",
                "run",
                "--detach",
                "--name",
                self.container,
                "--network=host",
                f"--env-file={environment}",
                "--volume",
                f"{config}:/app/config.yaml:ro",
                PINNED_IMAGE,
                "--config",
                "/app/config.yaml",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.proxy_port),
            ]
        )
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            status, _, _ = request_json(f"{self.base_url}/health/liveliness", timeout=3)
            if status == 200:
                return
            time.sleep(2)
        logs = run(["sudo", "-n", "podman", "logs", self.container], check=False)
        raise RuntimeError(
            f"LiteLLM fixture did not start:\n{logs.stdout}\n{logs.stderr}"
        )

    def stop(self) -> None:
        run(["sudo", "-n", "podman", "rm", "--force", self.container], check=False)
        if self.mock_started:
            self.mock_server.shutdown()
        self.mock_server.server_close()
        if self.database_created:
            self.psql(f"DROP DATABASE IF EXISTS {self.database} WITH (FORCE)")
        self.psql(f"DROP ROLE IF EXISTS {self.role}")
        if self.context_role_created:
            self.psql("DROP ROLE IF EXISTS eros_context")
        self.tempdir.cleanup()

    def generate_key(self, alias: str, max_budget: float) -> str:
        status, response, _ = request_json(
            f"{self.base_url}/key/generate",
            {
                "key_alias": alias,
                "models": ["claude-sonnet-4-6"],
                "max_budget": max_budget,
                "budget_duration": "1d",
                "object_permission": {
                    "mcp_servers": ["fixture"],
                    "mcp_tool_search_enabled": True,
                },
            },
            headers={"Authorization": f"Bearer {self.master_key}"},
        )
        if status != 200 or not response.get("key"):
            raise RuntimeError(f"key generation failed ({status}): {response}")
        return str(response["key"])

    def apply_policy_twice(self) -> None:
        for _ in range(2):
            run(
                [
                    "sudo",
                    "-n",
                    "-u",
                    "postgres",
                    "psql",
                    "--dbname",
                    self.database,
                    "--no-psqlrc",
                    "--set",
                    "ON_ERROR_STOP=1",
                    "--file",
                    str(self.policy_path),
                ]
            )

    def mcp_call(
        self, key: str, method: str, params: dict[str, Any], session_id: str = ""
    ) -> tuple[Any, str]:
        headers = {"Authorization": f"Bearer {key}"}
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        if not method.startswith("notifications/"):
            payload["id"] = uuid.uuid4().hex
        status, response, response_headers = request_json(
            f"{self.base_url}/mcp/", payload, headers=headers
        )
        if status not in (200, 202) or response.get("error"):
            raise RuntimeError(f"MCP {method} failed ({status}): {response}")
        return response, response_headers.get("mcp-session-id", session_id)

    def verify_mcp(self, key: str) -> dict[str, Any]:
        initialized, session_id = self.mcp_call(
            key,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "eros-integration", "version": "1"},
            },
        )
        self.mcp_call(key, "notifications/initialized", {}, session_id)
        listed, session_id = self.mcp_call(key, "tools/list", {}, session_id)
        tools = listed.get("result", {}).get("tools", [])
        names = {tool.get("name") for tool in tools}
        if names != VIRTUAL_TOOLS:
            raise AssertionError(
                f"expected exactly {sorted(VIRTUAL_TOOLS)}, got {sorted(names)}"
            )
        searched, session_id = self.mcp_call(
            key,
            "tools/call",
            {
                "name": "mcp_tool_search",
                "arguments": {"query": "echo integration fixture", "top_k": 3},
            },
            session_id,
        )
        content = searched.get("result", {}).get("content", [])
        matches = json.loads(content[0]["text"]) if content else []
        if len(matches) != 1 or "fixture_echo" not in str(matches[0].get("name")):
            raise AssertionError(f"virtual tool search failed: {matches}")
        tool_name = str(matches[0]["name"])
        called, _ = self.mcp_call(
            key,
            "tools/call",
            {
                "name": "mcp_tool_call",
                "arguments": {"tool_name": tool_name, "arguments": {"text": "MCP_OK"}},
            },
            session_id,
        )
        if "MCP_OK" not in json.dumps(called):
            raise AssertionError(f"virtual tool call failed: {called}")
        return {
            "initialize": initialized.get("result", {}).get("serverInfo"),
            "tools": sorted(names),
            "called": tool_name,
        }

    def verify_policy(self) -> dict[str, Any]:
        output = self.psql(
            """SELECT json_agg(row_to_json(result)) FROM (
                 SELECT key_alias, max_budget, budget_duration,
                        metadata->>'budget_mode' AS budget_mode,
                        metadata->>'hard_budget' AS hard_budget
                 FROM \"LiteLLM_VerificationToken\"
                 WHERE key_alias IN ('eros-integration-test', 'eros-integration-operational', 'eros-interop-test')
                 ORDER BY key_alias
               ) result""",
            self.database,
        )
        rows = json.loads(output)
        by_alias = {row["key_alias"]: row for row in rows}
        operational = by_alias["eros-integration-operational"]
        if (
            operational["max_budget"] is not None
            or operational["budget_duration"] is not None
        ):
            raise AssertionError(
                f"operational hard budget survived policy: {operational}"
            )
        if operational["budget_mode"] != "observe":
            raise AssertionError(f"operational budget is not advisory: {operational}")
        for alias in ("eros-integration-test", "eros-interop-test"):
            if (
                by_alias[alias]["max_budget"] is None
                or by_alias[alias]["hard_budget"] != "true"
            ):
                raise AssertionError(
                    f"explicit test hard budget was not preserved: {by_alias[alias]}"
                )
        return by_alias

    def verify_cache_and_tags(self, key: str) -> dict[str, Any]:
        session = f"integration-{uuid.uuid4().hex}"
        headers = {
            "Authorization": f"Bearer {key}",
            "x-eros-consumer": "integration",
            "x-eros-trust-domain": "shared",
            "x-eros-workload": "pinned-fixture",
            "x-eros-session": session,
        }
        prefix = f"{session} " + " ".join(["stable-prefix"] * 1800)
        payload = {
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "system", "content": prefix},
                {"role": "user", "content": "Reply with exactly CACHE_OK"},
            ],
            "max_tokens": 16,
            "temperature": 0,
        }
        responses = []
        for _ in range(2):
            status, response, _ = request_json(
                f"{self.base_url}/v1/chat/completions",
                payload,
                headers=headers,
                timeout=90,
            )
            if status != 200:
                raise RuntimeError(f"cache canary failed ({status}): {response}")
            responses.append(response.get("id"))
        deadline = time.monotonic() + 45
        rows: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            output = self.psql(
                f"""SELECT coalesce(json_agg(row_to_json(result)), '[]'::json) FROM (
                       SELECT spend, request_tags,
                              coalesce(NULLIF(metadata #>> '{{usage_object,cache_read_input_tokens}}', '')::bigint, 0)
                                AS cache_read_input_tokens,
                              coalesce(NULLIF(metadata #>> '{{usage_object,cache_creation_input_tokens}}', '')::bigint, 0)
                                AS cache_creation_input_tokens
                       FROM \"LiteLLM_SpendLogs\"
                       WHERE request_tags::text LIKE '%{session}%'
                       ORDER BY \"startTime\"
                     ) result""",
                self.database,
            )
            rows = json.loads(output)
            if len(rows) >= 2:
                break
            time.sleep(2)
        if len(rows) < 2:
            raise AssertionError(f"expected two tagged spend rows, got {rows}")
        serialized_tags = json.dumps([row.get("request_tags") for row in rows])
        for expected in ("integration", "shared", "pinned-fixture", session):
            if expected not in serialized_tags:
                raise AssertionError(
                    f"missing attribution tag {expected}: {serialized_tags}"
                )
        if max(int(row.get("cache_read_input_tokens") or 0) for row in rows) <= 0:
            raise AssertionError(
                f"second call did not produce provider cache-read evidence: {rows}"
            )
        return {"response_ids": responses, "spend_rows": rows}

    def interrupt_stream(self, key: str) -> tuple[int, str]:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.proxy_port, timeout=45
        )
        payload = json.dumps(
            {
                "model": "claude-sonnet-4-6",
                "max_tokens": 4096,
                "stream": True,
                "messages": [
                    {
                        "role": "user",
                        "content": "Write a very long numbered sequence with detailed explanations and do not stop early.",
                    }
                ],
            }
        )
        connection.request(
            "POST",
            "/v1/messages",
            body=payload,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
        )
        response = connection.getresponse()
        body = response.read(512).decode(errors="replace")
        connection.close()
        return response.status, body

    def verify_interrupted_stream_budget(self, key: str) -> dict[str, Any]:
        attempts = []
        for _ in range(3):
            status, body = self.interrupt_stream(key)
            attempts.append({"status": status, "body": body[-300:]})
            if status == 429:
                break
            time.sleep(1)
        time.sleep(4)
        row = json.loads(
            self.psql(
                """SELECT row_to_json(result) FROM (
                     SELECT spend, max_budget, budget_duration
                     FROM \"LiteLLM_VerificationToken\" WHERE key_alias = 'eros-interop-test'
                   ) result""",
                self.database,
            )
        )
        false_429 = any(item["status"] == 429 for item in attempts) and float(
            row.get("spend") or 0
        ) < float(row["max_budget"])
        return {
            "attempts": attempts,
            "database": row,
            "false_429_reproduced": false_429,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path(__file__).with_name("litellm-policy.sql"),
    )
    parser.add_argument("--disable-budget-reservation", action="store_true")
    arguments = parser.parse_args()
    fixture = Fixture(arguments.policy, arguments.disable_budget_reservation)
    evidence: dict[str, Any] = {
        "image": PINNED_IMAGE,
        "disable_budget_reservation": arguments.disable_budget_reservation,
    }
    try:
        fixture.start()
        integration_key = fixture.generate_key("eros-integration-test", 2.0)
        fixture.generate_key("eros-integration-operational", 2.0)
        interop_key = fixture.generate_key("eros-interop-test", 0.10)
        fixture.apply_policy_twice()
        evidence["policy"] = fixture.verify_policy()
        evidence["mcp"] = fixture.verify_mcp(integration_key)
        evidence["cache_and_tags"] = fixture.verify_cache_and_tags(integration_key)
        evidence["interrupted_stream"] = fixture.verify_interrupted_stream_budget(
            interop_key
        )
        if (
            arguments.disable_budget_reservation
            and evidence["interrupted_stream"]["false_429_reproduced"]
        ):
            raise AssertionError("false budget 429 survived disable_budget_reservation")
        print(json.dumps(evidence, indent=2, default=str))
    finally:
        fixture.stop()


if __name__ == "__main__":
    main()
