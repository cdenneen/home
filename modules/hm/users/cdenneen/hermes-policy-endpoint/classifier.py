"""
Outage classifier - decides whether Eros is qualified-unavailable, without
depending on Eros, LiteLLM, Postgres, or DNS to make that decision
(execution-contract.md 10.1/10.2).

Two independent, low-level probes:
  1. Raw TCP connect to Eros's pinned Tailscale IP:port. This never needs
     DNS. It is used ONLY to classify health, never as the actual forward
     target for a real request in NORMAL mode (execution-contract.md 10:
     "the diagnostic IP remains diagnostic only").
  2. HTTP GET of LiteLLM's health endpoint via the stable service name, if
     it resolves. DNS failure here is a *signal*, not itself sufficient
     evidence of an outage (BOOT-... "stable private DNS fails but Eros
     host health is known" - qualified DNS-outage policy, not blind
     escalation).

Never-sufficient-alone signals (HTTP 429, budget/auth/policy denial,
capability mismatch, malformed output) are explicitly NOT evaluated here.
This module only sees TCP/HTTP-health-level signals; the governor layer is
responsible for keeping economic-denial responses out of this path
entirely (they must not even be passed to the classifier).
"""

import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field


@dataclass
class Probe:
    ok: bool
    detail: str
    ts: float = field(default_factory=time.time)


def tcp_probe(host: str, port: int, timeout: float = 2.0) -> Probe:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return Probe(True, f"tcp connect {host}:{port} ok")
    except OSError as exc:
        return Probe(False, f"tcp connect {host}:{port} failed: {exc}")


def http_health_probe(url: str, timeout: float = 3.0) -> Probe:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if 200 <= resp.status < 300:
                return Probe(True, f"http health {url} -> {resp.status}")
            return Probe(False, f"http health {url} -> {resp.status}")
    except urllib.error.URLError as exc:
        return Probe(False, f"http health {url} failed: {exc}")
    except OSError as exc:
        return Probe(False, f"http health {url} failed: {exc}")


class OutageClassifier:
    """
    Bounded-repeated-probe classifier with hysteresis, per
    execution-contract.md 10.1: escalate on N consecutive failures across
    both probes over a bounded window; recover only after M consecutive
    successes. Ambiguous (probes disagree, or flapping) => not qualified
    for CONTINUITY-AUTO; that is BREAK-GLASS territory (human judgment).
    """

    def __init__(
        self,
        *,
        eros_ip: str,
        eros_port: int,
        eros_health_url: str | None,
        escalate_after: int = 3,
        recover_after: int = 3,
        probe_interval_s: float = 5.0,
    ):
        self.eros_ip = eros_ip
        self.eros_port = eros_port
        self.eros_health_url = eros_health_url
        self.escalate_after = escalate_after
        self.recover_after = recover_after
        self.probe_interval_s = probe_interval_s
        self._consecutive_fail = 0
        self._consecutive_ok = 0
        self._last_probe_ts = 0.0
        self._last_evidence: list[Probe] = []

    def probe_now(self) -> list[Probe]:
        probes = [tcp_probe(self.eros_ip, self.eros_port)]
        if self.eros_health_url:
            probes.append(http_health_probe(self.eros_health_url))
        self._last_probe_ts = time.time()
        self._last_evidence = probes
        return probes

    def observe(self) -> str:
        """Returns one of: 'healthy', 'qualified_outage', 'ambiguous'."""
        probes = self.probe_now()
        all_ok = all(p.ok for p in probes)
        all_fail = all(not p.ok for p in probes)

        if all_ok:
            self._consecutive_ok += 1
            self._consecutive_fail = 0
        elif all_fail:
            self._consecutive_fail += 1
            self._consecutive_ok = 0
        else:
            # Probes disagree (e.g. TCP up but HTTP down) - never
            # confidently qualified; don't let it silently count as either
            # streak so a flapping split-signal can't slowly accumulate
            # into an automatic escalation.
            self._consecutive_fail = 0
            self._consecutive_ok = 0
            return "ambiguous"

        if self._consecutive_fail >= self.escalate_after:
            return "qualified_outage"
        if self._consecutive_ok >= self.recover_after:
            return "healthy"
        # Still within hysteresis band - report the last confident state
        # rather than flapping; caller should treat "ambiguous" here as
        # "no change yet", not as evidence either way.
        return "ambiguous"

    def evidence(self) -> dict:
        return {
            "eros_ip": self.eros_ip,
            "eros_port": self.eros_port,
            "eros_health_url": self.eros_health_url,
            "probes": [
                {"ok": p.ok, "detail": p.detail, "ts": p.ts}
                for p in self._last_evidence
            ],
            "consecutive_fail": self._consecutive_fail,
            "consecutive_ok": self._consecutive_ok,
        }
