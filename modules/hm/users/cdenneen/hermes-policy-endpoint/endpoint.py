#!/usr/bin/env python3
"""
Host-local policy endpoint (Bootstrap tier).

Hermes points model.base_url at this service. It never talks to Eros or
a direct provider itself. This process:

  - forwards to the stable Eros LiteLLM endpoint in NORMAL/CONSERVE/
    DEGRADED/CRITICAL economic states, using the actor's dedicated Eros
    virtual key;
  - validates that every successful response carries a real, present cost
    (unknown/missing cost freezes that route - never treated as $0);
  - classifies Eros health independently of Eros/Postgres/DNS
    (classifier.py) and, only on a qualified outage, activates
    CONTINUITY-AUTO through a separately-provisioned emergency credential
    (continuity.py) - denying rather than falling back to a normal key if
    that credential isn't available;
  - supports explicit human BREAK-GLASS activation for ambiguous cases;
  - applies Bootstrap deterministic economic-state admission
    (governor.py) - fixed seed thresholds, no adaptive learning;
  - records everything locally (state.py) so accounting survives Eros
    being completely unavailable, and so continuity episodes can be
    reconciled back into the EPR once Eros recovers.

Not implemented in this pass (see bootstrap-gate-evidence.md): a network
control endpoint for BREAK-GLASS (uses a local flag file instead), and
real emergency-credential provisioning (console/IAM actions requiring
separate explicit authorization).
"""

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from classifier import OutageClassifier
from continuity import ContinuityController, ContinuityDenied, EmergencyCredentialStore
from governor import (
    DEFAULT_THRESHOLDS,
    Admission,
    EconomicState,
    Priority,
    admission_decision,
    compute_state,
)
from state import LocalState

HERE = os.path.dirname(os.path.abspath(__file__))


def load_config(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


class PolicyEndpoint:
    def __init__(self, config: dict):
        self.config = config
        self.actor = config["actor"]
        self.priority = Priority(config.get("priority", "P2"))
        self.continuity_class = config.get("continuity_class", "automatic-read-only")
        self.eros_base_url = config["eros_base_url"].rstrip("/")
        self.eros_api_key = config["eros_api_key"]
        self.monthly_budget = float(config.get("monthly_budget_usd", 20.0))
        self.expected_burn_1h = float(config.get("expected_burn_1h_usd", 0.5))

        self.state = LocalState(Path(config["state_db_path"]))
        self.classifier = OutageClassifier(
            eros_ip=config["eros_tailscale_ip"],
            eros_port=int(config.get("eros_port", 4000)),
            eros_health_url=f"{self.eros_base_url}/health/liveliness",
        )
        self.continuity = ContinuityController(
            state=self.state,
            credential_store=EmergencyCredentialStore(
                Path(config["emergency_credential_path"])
                if config.get("emergency_credential_path")
                else None
            ),
            break_glass_flag_path=Path(config["break_glass_flag_path"]),
        )
        self.frozen_routes: set[str] = set()
        self._classifier_lock = threading.Lock()
        self._last_classification = "healthy"
        self._stop = threading.Event()
        self._bg_thread = threading.Thread(target=self._background_probe_loop, daemon=True)

    def start_background_probing(self):
        self._bg_thread.start()

    def stop(self):
        self._stop.set()

    def _background_probe_loop(self):
        while not self._stop.is_set():
            try:
                result = self.classifier.observe()
                with self._classifier_lock:
                    if result in ("healthy", "qualified_outage"):
                        self._last_classification = result
                if result == "healthy":
                    self.continuity.end_continuity_auto_if_recovered()
            except Exception as exc:  # noqa: BLE001 - probing must never crash the loop
                print(f"[probe-loop] error: {exc}", file=sys.stderr)
            self._stop.wait(self.classifier.probe_interval_s)

    def current_mode(self) -> str:
        """'normal' | 'continuity_auto' | 'break_glass'"""
        if self.continuity.break_glass_active():
            return "break_glass"
        with self._classifier_lock:
            classification = self._last_classification
        if classification == "qualified_outage":
            return "continuity_auto"
        return "normal"

    # --- economic state --------------------------------------------------------

    def economic_state(self) -> tuple[EconomicState, str]:
        now = time.time()
        month_start = now - (now % (30 * 24 * 3600))  # bootstrap approx; real impl should use calendar month start
        mtd = self.state.burn_since(self.actor, month_start)
        burn_1h = self.state.burn_since(self.actor, now - 3600)
        if mtd["unknown_count"] > 0 or burn_1h["unknown_count"] > 0:
            # Unknown cost anywhere in the window means the window is not
            # trustworthy for admission math - fail toward the conservative
            # side rather than pretending unknown == 0.
            return EconomicState.CRITICAL, "unknown-cost events present in burn window; treating conservatively"
        return compute_state(
            monthly_budget=self.monthly_budget,
            mtd_cost=mtd["known_cost"],
            burn_1h=burn_1h["known_cost"],
            expected_burn_1h=self.expected_burn_1h,
            now_ts=now,
            thresholds=DEFAULT_THRESHOLDS,
        )

    # --- forwarding --------------------------------------------------------------

    def forward_normal(self, requested_route: str, body: bytes) -> tuple[int, bytes, dict]:
        if requested_route in self.frozen_routes:
            return 409, json.dumps({
                "error": {"message": f"route {requested_route} is frozen pending cost-integrity review", "type": "route_frozen"}
            }).encode(), {}

        req = urllib.request.Request(
            f"{self.eros_base_url}/v1/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.eros_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                raw = resp.read()
                return resp.status, raw, self._extract_attestation(raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            return exc.code, raw, {}
        except urllib.error.URLError as exc:
            # Network-level failure talking to Eros. This is exactly the
            # kind of signal the classifier (not this method) should be
            # accumulating - this single failed call is NOT itself
            # sufficient to declare an outage or activate continuity.
            return 502, json.dumps({
                "error": {"message": f"eros unreachable: {exc}", "type": "eros_network_error"}
            }).encode(), {}

    @staticmethod
    def _extract_attestation(raw: bytes) -> dict:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        usage = data.get("usage", {})
        return {
            "actual_model": data.get("model"),
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            # LiteLLM doesn't echo cost in the OpenAI-compatible response
            # body by default; the Bootstrap governor treats "no cost
            # field visible here" as unknown for THIS response and relies
            # on the /spend/logs reconciliation pass (not implemented in
            # this slice) to fill it in from Eros's authoritative
            # LiteLLM_SpendLogs rather than ever assuming $0.
            "cost_usd": data.get("_response_cost"),
        }

    def handle_chat_completion(self, priority: Priority, body: bytes) -> tuple[int, bytes]:
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return 400, json.dumps({"error": {"message": "invalid JSON body"}}).encode()
        requested_route = parsed.get("model", "")

        mode = self.current_mode()

        if mode in ("continuity_auto", "break_glass"):
            if priority not in (Priority.P0, Priority.P1):
                self.state.record_admission(
                    actor=self.actor, priority=priority.value,
                    continuity_class=self.continuity_class,
                    requested_route=requested_route, decision="deny",
                    reason=f"{mode}: P2/P3 suspended during continuity",
                    economic_state=EconomicState.BREAK_GLASS.value,
                )
                return 403, json.dumps({
                    "error": {"message": "P2/P3 suspended during continuity", "type": "continuity_priority_denied"}
                }).encode()
            try:
                cred = (
                    self.continuity.activate_continuity_auto(self.classifier.evidence())
                    if mode == "continuity_auto"
                    else self._break_glass_credential()
                )
            except ContinuityDenied as exc:
                self.state.record_admission(
                    actor=self.actor, priority=priority.value,
                    continuity_class=self.continuity_class,
                    requested_route=requested_route, decision="deny",
                    reason=f"continuity denied: {exc}",
                    economic_state=EconomicState.BREAK_GLASS.value,
                )
                # BOOT-013: emergency credential retrieval fails -> deny,
                # alert, never substitute a normal credential.
                return 503, json.dumps({
                    "error": {"message": f"continuity unavailable: {exc}", "type": "continuity_credential_unavailable"}
                }).encode()
            return self._forward_continuity(cred, requested_route, body, priority)

        # mode == "normal"
        state, reason = self.economic_state()
        decision = admission_decision(state, priority)
        self.state.record_admission(
            actor=self.actor, priority=priority.value,
            continuity_class=self.continuity_class,
            requested_route=requested_route, decision=decision.value,
            reason=reason, economic_state=state.value,
        )
        if decision == Admission.DENY:
            return 403, json.dumps({
                "error": {"message": f"denied by economic state {state.value}: {reason}", "type": "economic_state_denied"}
            }).encode()
        if decision == Admission.QUEUE:
            return 429, json.dumps({
                "error": {"message": f"queued by economic state {state.value}: {reason}", "type": "economic_state_queued"}
            }).encode()

        status, raw, attestation = self.forward_normal(requested_route, body)
        if status == 200:
            cost = attestation.get("cost_usd")
            if cost is None:
                # Unknown cost on an otherwise-successful call: freeze this
                # route for future admission and record the event with
                # cost_usd=None (never 0.0).
                self.frozen_routes.add(requested_route)
            self.state.record_spend(
                actor=self.actor, requested_route=requested_route,
                actual_provider=None, actual_model=attestation.get("actual_model"),
                cost_usd=cost, input_tokens=attestation.get("input_tokens"),
                output_tokens=attestation.get("output_tokens"), source="eros",
            )
        return status, raw

    def _break_glass_credential(self):
        return self.continuity.credential_store.load()

    def _forward_continuity(self, cred, requested_route, body, priority):
        idempotency_key = None
        try:
            parsed = json.loads(body)
            idempotency_key = parsed.get("idempotency_key")
        except json.JSONDecodeError:
            pass
        digest = self.state.digest_for(self.actor, requested_route, body, idempotency_key)
        existing = self.state.idempotency_lookup(digest)
        if existing and existing.get("completed_at"):
            # Eros recovery / retry-after-continuity case: the same
            # mutating request must not execute twice
            # (execution-contract.md 10.5).
            cached = existing["response_json"]
            return 200, cached.encode() if isinstance(cached, str) else b"{}"

        self.state.idempotency_start(digest, self.actor)
        # Bootstrap: continuity model/route is restricted to the emergency
        # credential's own single model - never the normal tier catalog.
        response_body = json.dumps({
            "id": "continuity-bootstrap",
            "model": cred.model,
            "choices": [{"message": {"role": "assistant", "content": "[continuity path - stub, no live emergency credential provisioned]"}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }).encode()
        self.state.idempotency_complete(digest, response_body.decode())
        self.state.record_spend(
            actor=self.actor, requested_route=requested_route,
            actual_provider=cred.provider, actual_model=cred.model,
            cost_usd=0.0, input_tokens=0, output_tokens=0, source="continuity",
            request_digest=digest,
        )
        return 200, response_body


class Handler(BaseHTTPRequestHandler):
    endpoint: PolicyEndpoint = None  # set by main()

    def _priority_for_request(self) -> Priority:
        # Bootstrap: priority comes from the actor's static config, not a
        # per-request signal Hermes can't yet reliably emit
        # (00-program-spec.md: unknown/unverifiable lineage is autonomous).
        return self.endpoint.priority

    def do_POST(self):
        if self.path not in ("/v1/chat/completions",):
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        status, raw = self.endpoint.handle_chat_completion(self._priority_for_request(), body)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            body = json.dumps({
                "mode": self.endpoint.current_mode(),
                "actor": self.endpoint.actor,
            }).encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, fmt, *args):  # noqa: A003 - quiet by default
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--port", type=int, default=8600)
    args = parser.parse_args()

    config = load_config(Path(args.config))
    endpoint = PolicyEndpoint(config)
    endpoint.start_background_probing()

    Handler.endpoint = endpoint
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"hermes-policy-endpoint listening on 127.0.0.1:{args.port} for actor={endpoint.actor}")
    try:
        server.serve_forever()
    finally:
        endpoint.stop()


if __name__ == "__main__":
    main()
