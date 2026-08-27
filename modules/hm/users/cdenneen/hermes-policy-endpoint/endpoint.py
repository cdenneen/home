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

from action_classification import (
    CONTINUITY_ORDER,
    continuity_mode_permits,
    effective_continuity_class,
    source_ceiling,
)
from credential_ceiling import RouteDenied, check_route_allowed, max_tier_of
from metadata_attestation import attested_source_for_peer
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
        # trust_domain -> agent -> workstream -> resource_project_context
        # -> workload/action. Required, no default - gateway/profile
        # identity (actor, above) must never silently stand in for these;
        # an instance missing any of them fails closed at startup rather
        # than guessing a trust domain.
        self.trust_domain = config["trust_domain"]
        if self.trust_domain not in ("work", "personal"):
            raise ValueError(f"trust_domain must be 'work' or 'personal', got {self.trust_domain!r}")
        self.agent = config["agent"]
        self.workstream = config["workstream"]
        self.resource_project_context = config.get("resource_project_context")
        self.priority = Priority(config.get("priority", "P2"))
        # Gateway-level ceiling only (continuity-class-audit.md) - the most
        # permissive continuity_class this actor could ever reach. Real
        # per-request classification (action_classification.py) takes the
        # more restrictive of this ceiling and the request's own
        # tool/source-derived classification; it never widens past this.
        self.continuity_class = config.get("continuity_class", "automatic-read-only")
        # #41: administratively bound route/tier ceiling, mirroring this
        # actor's real Eros virtual key allowlist. ATTESTED (config-only,
        # required, no default) - never derived from the request body.
        # Enforced here as defense in depth alongside LiteLLM's own
        # independent model allowlist, not as a replacement for it.
        self.allowed_routes = frozenset(config["allowed_routes"])
        if not self.allowed_routes:
            raise ValueError("allowed_routes must be non-empty - an actor with no allowed routes can never be admitted")
        self.max_tier = max_tier_of(self.allowed_routes)
        self.eros_base_url = config["eros_base_url"].rstrip("/")
        # Read at process startup, not baked into the rendered config file -
        # sops-nix materializes this path at its own activation/runtime
        # ordering, which may run after this service's config is rendered.
        with open(config["eros_api_key_file"]) as f:
            self.eros_api_key = f.read().strip()
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

    def _resolve_workload_source(self, asserted_source, peer_port, local_port):
        """#40: upgrade the request-body-ASSERTED x_hermes_source to
        ATTESTED when the kernel-guaranteed TCP peer's own side channel
        (written by the sitecustomize patch, keyed by its own pid) is
        available and agrees. On disagreement, resolve to whichever
        source's ceiling is more restrictive - a conflict must never
        make the request more permissive (#39C). Returns
        (resolved_source, provenance, conflict_evidence_or_None)."""
        if peer_port is None or local_port is None:
            return asserted_source, "asserted", None
        attested, attempted = attested_source_for_peer(local_port, peer_port)
        if not attempted or attested is None:
            return asserted_source, "asserted", None
        if asserted_source is None:
            return attested, "attested", None
        if attested == asserted_source:
            return attested, "attested", None
        # Disagreement: keep whichever yields the stricter ceiling.
        stricter = (
            attested
            if CONTINUITY_ORDER.index(source_ceiling(attested))
            >= CONTINUITY_ORDER.index(source_ceiling(asserted_source))
            else asserted_source
        )
        conflict = {"asserted": asserted_source, "attested": attested, "resolved": stricter}
        return stricter, "asserted_conflict_resolved_to_stricter", conflict

    def handle_chat_completion(
        self, priority: Priority, body: bytes, peer_port: int | None = None, local_port: int | None = None,
    ) -> tuple[int, bytes]:
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return 400, json.dumps({"error": {"message": "invalid JSON body"}}).encode()
        requested_route = parsed.get("model", "")

        # #41: administratively bound route/tier ceiling - checked first,
        # before classification/economic-state/forwarding, so an out-of-
        # envelope request is denied before any provider spend. A caller
        # cannot widen this by supplying a different route in the body
        # than what it's actually asking to be admitted for - the
        # requested_route IS the value checked, there is no separate
        # "claimed tier" field this could disagree with. An empty/missing/
        # unrecognized route is denied the same way an explicitly
        # disallowed one is - never a permissive default.
        try:
            check_route_allowed(requested_route, self.allowed_routes)
        except RouteDenied as exc:
            self.state.record_admission(
                actor=self.actor, priority=priority.value,
                continuity_class=self.continuity_class,
                requested_route=requested_route, decision="deny",
                reason=f"credential ceiling: {exc}",
                economic_state=EconomicState.NORMAL.value,
                trust_domain=self.trust_domain, agent=self.agent,
                workstream=self.workstream,
                resource_project_context=self.resource_project_context,
            )
            return 403, json.dumps({
                "error": {"message": str(exc), "type": "route_outside_credential_ceiling"}
            }).encode()

        # Piece 1 (tool-name allowlist, unknown -> deny automatic) and
        # piece 2 (x_hermes_source, propagated via the workload-metadata
        # sitecustomize patch) combine here into one effective per-request
        # continuity_class - never more permissive than the gateway ceiling.
        tool_names = [
            t.get("function", {}).get("name")
            for t in (parsed.get("tools") or [])
            if isinstance(t, dict)
        ]
        tool_names = [name for name in tool_names if name]
        asserted_source = parsed.get("x_hermes_source")
        workload_source, source_provenance, metadata_conflict = self._resolve_workload_source(
            asserted_source, peer_port, local_port
        )
        effective_class, classification_evidence = effective_continuity_class(
            self.continuity_class, tool_names, workload_source
        )
        classification_evidence["source_ceiling_provenance"] = source_provenance
        if metadata_conflict is not None:
            classification_evidence["metadata_conflict"] = metadata_conflict

        # x_hermes_* fields are for this endpoint's own classification only -
        # confirmed live (nyx-gitlab, 2026-08-27): forwarding x_hermes_source
        # verbatim to Eros/Bedrock fails with "Extra inputs are not
        # permitted" (Bedrock's strict request schema rejects unrecognized
        # top-level fields). Strip every x_hermes_* key before forwarding;
        # everything downstream (Eros forwarding, idempotency digest) uses
        # this sanitized body, never the original.
        hermes_meta_keys = [k for k in parsed if k.startswith("x_hermes_")]
        if hermes_meta_keys:
            sanitized = {k: v for k, v in parsed.items() if k not in hermes_meta_keys}
            body = json.dumps(sanitized).encode()

        idempotency_key = parsed.get("idempotency_key")
        digest = self.state.digest_for(self.actor, requested_route, body, idempotency_key)

        mode = self.current_mode()

        if mode in ("continuity_auto", "break_glass"):
            if priority not in (Priority.P0, Priority.P1):
                self.state.record_admission(
                    actor=self.actor, priority=priority.value,
                    continuity_class=effective_class,
                    requested_route=requested_route, decision="deny",
                    reason=f"{mode}: P2/P3 suspended during continuity",
                    economic_state=EconomicState.BREAK_GLASS.value,
                    classification_evidence=classification_evidence,
                    trust_domain=self.trust_domain, agent=self.agent,
                    workstream=self.workstream,
                    resource_project_context=self.resource_project_context,
                )
                return 403, json.dumps({
                    "error": {"message": "P2/P3 suspended during continuity", "type": "continuity_priority_denied"}
                }).encode()
            if not continuity_mode_permits(mode, effective_class):
                # continuity_class is orthogonal to priority
                # (execution-contract.md 10.4): a P0/P1 request whose
                # effective classification is human-present or stricter is
                # still denied under continuity_auto, since no human is
                # present by construction in that mode.
                self.state.record_admission(
                    actor=self.actor, priority=priority.value,
                    continuity_class=effective_class,
                    requested_route=requested_route, decision="deny",
                    reason=f"{mode}: effective continuity_class '{effective_class}' not permitted under this mode",
                    economic_state=EconomicState.BREAK_GLASS.value,
                    classification_evidence=classification_evidence,
                    trust_domain=self.trust_domain, agent=self.agent,
                    workstream=self.workstream,
                    resource_project_context=self.resource_project_context,
                )
                return 403, json.dumps({
                    "error": {
                        "message": f"continuity_class '{effective_class}' not permitted under {mode}",
                        "type": "continuity_class_denied",
                    }
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
                    continuity_class=effective_class,
                    requested_route=requested_route, decision="deny",
                    reason=f"continuity denied: {exc}",
                    economic_state=EconomicState.BREAK_GLASS.value,
                    classification_evidence=classification_evidence,
                    trust_domain=self.trust_domain, agent=self.agent,
                    workstream=self.workstream,
                    resource_project_context=self.resource_project_context,
                )
                # BOOT-013: emergency credential retrieval fails -> deny,
                # alert, never substitute a normal credential.
                return 503, json.dumps({
                    "error": {"message": f"continuity unavailable: {exc}", "type": "continuity_credential_unavailable"}
                }).encode()
            return self._forward_continuity(cred, requested_route, body, priority, digest)

        # mode == "normal"
        #
        # execution-contract.md 10.5 / BOOT-028: Eros restoration must not
        # replay an in-flight or completed continuity action merely because
        # the normal route became available again. The mutating action may
        # have completed via CONTINUITY-AUTO/BREAK-GLASS moments ago, then
        # the caller resubmits the identical request now that mode has
        # reverted to "normal" - this check, not just the one inside
        # _forward_continuity, is what actually closes that path, since a
        # resubmission after recovery arrives here, not through continuity.
        existing = self.state.idempotency_lookup(digest)
        if existing and existing.get("completed_at"):
            cached = existing["response_json"]
            return 200, cached.encode() if isinstance(cached, str) else b"{}"

        state, reason = self.economic_state()
        decision = admission_decision(state, priority)
        self.state.record_admission(
            actor=self.actor, priority=priority.value,
            continuity_class=effective_class,
            requested_route=requested_route, decision=decision.value,
            reason=reason, economic_state=state.value,
            classification_evidence=classification_evidence,
            trust_domain=self.trust_domain, agent=self.agent,
            workstream=self.workstream,
            resource_project_context=self.resource_project_context,
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
                trust_domain=self.trust_domain, agent=self.agent,
                workstream=self.workstream,
                resource_project_context=self.resource_project_context,
            )
        return status, raw

    def _break_glass_credential(self):
        return self.continuity.credential_store.load()

    def _forward_continuity(self, cred, requested_route, body, priority, digest):
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
            trust_domain=self.trust_domain, agent=self.agent,
            workstream=self.workstream,
            resource_project_context=self.resource_project_context,
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
        status, raw = self.endpoint.handle_chat_completion(
            self._priority_for_request(), body,
            peer_port=self.client_address[1], local_port=self.server.server_address[1],
        )
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
                "trust_domain": self.endpoint.trust_domain,
                "agent": self.endpoint.agent,
                "workstream": self.endpoint.workstream,
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
