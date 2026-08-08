import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import pytest
from axis_watchdog.engine import Watchdog
from axis_watchdog.projection import CanonicalSlackProjector, SlackProjector
from axis_watchdog.recovery import RecoveryExecutor

ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR_ROOT = ROOT.parent / "hermes-supervisor"


class FakeProjector:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = []

    def project(self, report, incidents, at_epoch):
        self.calls.append((report, incidents, at_epoch))
        if self.fail:
            raise RuntimeError("projection unavailable")
        return [
            {
                "target_type": "dashboard",
                "target_id": "overview",
                "operation": "verified",
                "status": "verified",
                "channel": "D1",
                "ts": "1.0",
                "fingerprint": "f1",
            }
        ]


class FakeDiagnostic:
    def __init__(self):
        self.calls = []

    def __call__(self, anomalies, evidence, control):
        self.calls.append((anomalies, evidence, control))
        return "bounded read-only diagnosis"


class FakeRecovery:
    def __init__(self):
        self.calls = []

    def execute(self, incident, diagnosis, control, now):
        self.calls.append((incident.copy(), diagnosis, control, now))
        return (
            "in-progress",
            "projection scheduled",
        ) if incident["recovery_level"] == 1 else ("completed", "safe recovery complete")


def iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def setup_runtime(tmp_path: Path, now: int = 1_800_000_000):
    root = tmp_path / "watchdog"
    supervisor = tmp_path / "supervisor"
    jobs_path = tmp_path / "cron" / "jobs.json"
    control = json.loads((ROOT / "control.defaults.json").read_text())
    control["cron_job_id"] = "watchdog-id"
    write(root / "control.json", control)
    write(
        supervisor / "control.json",
        {
            "schema": "axis.external-development-supervisor.control",
            "schema_version": "1.0.0",
            "version": 4,
            "mode": "enabled",
            "kill_switch": False,
            "slack_user_id": "U1",
        },
    )
    write(supervisor / "inventory.json", {"generation_id": "inventory-1"})
    write(
        supervisor / "execution-graph.json",
        {
            "generation_id": "graph-1",
            "nodes": [
                {
                    "ref": "ghostspace/axis#1",
                    "classification": "Executable",
                    "semantic_record": None,
                }
            ],
        },
    )
    write(
        supervisor / "active-mission.json",
        {
            "schema": "axis.external-development-supervisor.active-mission",
            "schema_version": "3.0.0",
            "current_state": "active",
            "generated_actions": [
                {
                    "kind": "dispatch-executable",
                    "executable": True,
                    "target": "ghostspace/axis#1",
                    "engineering_purpose": "advance one verified gate",
                }
            ],
            "active_assignments": [],
            "completed_assignments": [],
            "external_blockers": [],
            "missing_gates": [
                {
                    "capability": "Web",
                    "gate": "verification",
                    "state": "pending",
                    "external_only": False,
                }
            ],
            "graduation_progress": {"graduated": 1, "total": 2},
            "effectiveness_metrics": {
                "assignments_evaluated": 2,
                "state_model_defects": 0,
                "effectiveness_percent": 100,
            },
        },
    )
    write(
        supervisor / "capability-graduation.json",
        {
            "primary_kpi": {"count": 1, "denominator": 2, "percent": 50},
            "capabilities": [
                {
                    "capability": "CLI",
                    "graduated": True,
                    "production_confidence": 100,
                    "first_failing_gate": None,
                    "graduation_state": {
                        "verification": {"applicable": True, "state": "passed"}
                    },
                    "product_subdimensions": {},
                },
                {
                    "capability": "Web",
                    "graduated": False,
                    "production_confidence": 50,
                    "first_failing_gate": "verification",
                    "graduation_state": {
                        "verification": {"applicable": True, "state": "pending"}
                    },
                    "product_subdimensions": {},
                },
            ],
            "milestones": [
                {
                    "milestone": "AX-M4",
                    "gate": "pending",
                    "denominator": {"graduated": 1},
                    "debts": [
                        {
                            "kind": "capability-gate",
                            "ref": "Web",
                            "gate": "verification",
                            "reason": "verification pending",
                        }
                    ],
                }
            ],
        },
    )
    write(supervisor / "capability-convergence.json", {"runtimes": []})
    write(
        supervisor / "slack-overview-state.json",
        {
            "schema": "axis.external-development-supervisor.slack-state",
            "schema_version": "1.1.0",
            "delivery_stage": "Slack_message_verified",
            "last_successful_update_epoch": now,
        },
    )
    write(
        jobs_path,
        {
            "jobs": [
                {
                    "id": "worker-id",
                    "name": "axis-development-supervisor-worker",
                    "enabled": True,
                    "last_run_at": iso(now - 60),
                },
                {
                    "id": "watchdog-id",
                    "name": "axis-development-watchdog",
                    "enabled": True,
                    "last_run_at": iso(now - 60),
                },
            ]
        },
    )
    return root, supervisor, jobs_path


def supervisor_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.glob("**/*")):
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def test_independence_both_directions_and_read_only_supervisor(tmp_path: Path):
    watchdog_source = "\n".join(
        path.read_text()
        for path in (ROOT / "scripts" / "axis_watchdog").glob("*.py")
    )
    supervisor_source = "\n".join(
        path.read_text()
        for path in (SUPERVISOR_ROOT / "scripts").glob("**/*.py")
    )
    assert "axis_supervisor" not in watchdog_source
    assert "axis_watchdog" not in supervisor_source

    root, supervisor, jobs = setup_runtime(tmp_path)
    before = supervisor_digest(supervisor)
    Watchdog(
        root,
        supervisor,
        jobs,
        clock=lambda: 1_800_000_000,
        projector=FakeProjector(),
        diagnostic=FakeDiagnostic(),
    ).run()
    assert supervisor_digest(supervisor) == before


def test_missed_heartbeat_is_incident_then_catches_up(tmp_path: Path):
    now = 1_800_000_000
    root, supervisor, jobs = setup_runtime(tmp_path, now)
    write(
        root / "heartbeat.json",
        {
            "schema": "axis.development-watchdog.heartbeat",
            "schema_version": "1.0.0",
            "cycle_id": "prior",
            "status": "completed",
            "started_at": iso(now - 1200),
            "started_at_epoch": now - 1200,
            "completed_at": iso(now - 1200),
            "completed_at_epoch": now - 1200,
        },
    )
    diagnostic = FakeDiagnostic()
    first = Watchdog(
        root,
        supervisor,
        jobs,
        clock=lambda: now,
        projector=FakeProjector(),
        diagnostic=diagnostic,
    ).run()
    assert "watchdog-heartbeat-missed" in first["anomalies"]
    assert diagnostic.calls == []

    second = Watchdog(
        root,
        supervisor,
        jobs,
        clock=lambda: now + 300,
        projector=FakeProjector(),
        diagnostic=diagnostic,
    ).run()
    assert "watchdog-heartbeat-missed" not in second["anomalies"]
    incidents = [json.loads(line) for line in (root / "incidents.jsonl").read_text().splitlines()]
    assert incidents[-1]["status"] == "resolved"
    assert "recovery-started" in [item["event"] for item in incidents]
    recoveries = [json.loads(line) for line in (root / "recoveries.jsonl").read_text().splitlines()]
    assert {item["action"] for item in recoveries} >= {
        "observe-and-catch-up",
        "deterministic-health-restored",
    }
    heartbeat_transitions = [
        item["transition"]
        for item in recoveries
        if item["action"] == "observe-and-catch-up"
    ]
    assert heartbeat_transitions == ["requested", "started", "completed"]


def test_product_progress_fingerprint_ignores_assignment_activity(tmp_path: Path):
    _root, supervisor, _jobs = setup_runtime(tmp_path)
    mission = json.loads((supervisor / "active-mission.json").read_text())
    graduation = json.loads((supervisor / "capability-graduation.json").read_text())
    original, _snapshot = Watchdog._mission_progress(mission, graduation)

    mission["active_assignments"] = [
        {
            "assignment_id": "assignment-2",
            "lifecycle_state": "running-code",
            "result_state": "pending",
        }
    ]
    mission["completed_assignments"] = [{"assignment_id": "assignment-1"}]
    mission["effectiveness_metrics"]["assignments_evaluated"] = 99
    unchanged, _snapshot = Watchdog._mission_progress(mission, graduation)
    assert unchanged == original

    graduation["capabilities"][1]["graduation_state"]["verification"]["state"] = "passed"
    gate_transition, _snapshot = Watchdog._mission_progress(mission, graduation)
    assert gate_transition != original
    graduation["milestones"][0]["debts"] = []
    debt_transition, _snapshot = Watchdog._mission_progress(mission, graduation)
    assert debt_transition != gate_transition


def test_expected_wait_does_not_hide_executable_evidence_actions():
    control = {"mode": "enabled", "kill_switch": False}
    mission = {
        "current_state": "blocked-external",
        "external_blockers": [{"ref": "external"}],
        "generated_actions": [
            {
                "kind": "collect-capability-evidence",
                "executable": True,
            }
        ],
    }
    expected, reason = Watchdog._expected_wait(control, mission, {"nodes": []})
    assert expected is False
    assert reason is None


def test_historical_stuck_anomaly_recovery_and_expected_wait(tmp_path: Path):
    now = 1_800_000_000
    root, supervisor, jobs = setup_runtime(tmp_path, now)
    projector = FakeProjector()
    diagnostic = FakeDiagnostic()
    Watchdog(
        root,
        supervisor,
        jobs,
        clock=lambda: now,
        projector=projector,
        diagnostic=diagnostic,
    ).run()
    state = json.loads((root / "state.json").read_text())
    state["mission_progress_since_epoch"] = now - 1801
    write(root / "state.json", state)
    heartbeat = json.loads((root / "heartbeat.json").read_text())
    heartbeat["completed_at_epoch"] = now
    heartbeat["completed_at"] = iso(now)
    write(root / "heartbeat.json", heartbeat)

    stuck = Watchdog(
        root,
        supervisor,
        jobs,
        clock=lambda: now + 1,
        projector=projector,
        diagnostic=diagnostic,
    ).run()
    assert "mission-progress-stuck" in stuck["anomalies"]
    recoveries = [
        json.loads(line) for line in (root / "recoveries.jsonl").read_text().splitlines()
    ]
    repair = [
        item for item in recoveries if item["action"] == "escalate-supervisor-repair"
    ]
    assert [item["transition"] for item in repair] == [
        "requested",
        "started",
        "completed",
    ]
    assert all(item["level"] == 4 and item["target"] == "cdenneen/home" for item in repair)
    assert len(diagnostic.calls) == 1
    escalation = json.loads(
        next((root / "repair-escalations").glob("*.json")).read_text()
    )
    assert escalation["repository"] == "cdenneen/home"
    assert escalation["product_dispatch_allowed"] is False

    graduation = json.loads((supervisor / "capability-graduation.json").read_text())
    graduation["primary_kpi"] = {"count": 2, "denominator": 2, "percent": 100}
    graduation["capabilities"][1]["graduated"] = True
    graduation["capabilities"][1]["graduation_state"]["verification"]["state"] = "passed"
    graduation["milestones"][0]["gate"] = "passed"
    graduation["milestones"][0]["denominator"]["graduated"] = 2
    graduation["milestones"][0]["debts"] = []
    write(supervisor / "capability-graduation.json", graduation)
    recovered = Watchdog(
        root,
        supervisor,
        jobs,
        clock=lambda: now + 301,
        projector=projector,
        diagnostic=diagnostic,
    ).run()
    assert "mission-progress-stuck" not in recovered["anomalies"]

    state = json.loads((root / "state.json").read_text())
    state["mission_progress_since_epoch"] = now - 7200
    write(root / "state.json", state)
    mission = json.loads((supervisor / "active-mission.json").read_text())
    mission["current_state"] = "blocked-external"
    mission["generated_actions"] = []
    mission["external_blockers"] = [
        {"ref": "decision", "kind": "product-owner", "reason": "approval required"}
    ]
    write(supervisor / "active-mission.json", mission)
    waiting = Watchdog(
        root,
        supervisor,
        jobs,
        clock=lambda: now + 601,
        projector=projector,
        diagnostic=diagnostic,
    ).run()
    assert "mission-progress-stuck" not in waiting["anomalies"]
    current = json.loads((root / "state.json").read_text())
    assert current["health"]["mission_progress"]["status"] == "waiting"


def test_no_product_dispatch_and_restart_preserves_lifecycle(tmp_path: Path):
    now = 1_800_000_000
    root, supervisor, jobs = setup_runtime(tmp_path, now)
    before = supervisor_digest(supervisor)
    diagnostic = FakeDiagnostic()
    first = Watchdog(
        root,
        supervisor,
        jobs,
        clock=lambda: now,
        projector=FakeProjector(fail=True),
        diagnostic=diagnostic,
    ).run()
    assert "slack-delivery-failed" in first["anomalies"]
    restarted = Watchdog(
        root,
        supervisor,
        jobs,
        clock=lambda: now + 300,
        projector=FakeProjector(fail=True),
        diagnostic=diagnostic,
    ).run()
    assert restarted["status"] == "degraded"
    state = json.loads((root / "state.json").read_text())
    assert state["cycle"] == 2
    assert diagnostic.calls == []
    assert supervisor_digest(supervisor) == before
    recoveries = [json.loads(line) for line in (root / "recoveries.jsonl").read_text().splitlines()]
    assert all(item["target"] in {"axis-development-watchdog", "cdenneen/home"} for item in recoveries)
    assert not any("ghostspace/axis" in json.dumps(item) for item in recoveries)


def incident(at_epoch: int = 1_800_000_000) -> dict:
    return {
        "incident_id": "wd-1",
        "status": "recovering",
        "anomaly_code": "test-anomaly",
        "summary": "test anomaly",
        "recovery_level": 1,
        "repair_repository": None,
        "observed_at": iso(at_epoch),
        "diagnosis": None,
    }


def test_incident_projection_preserves_canonical_assignment_and_decision_maps(
    tmp_path: Path,
):
    supervisor = tmp_path / "supervisor"
    write(supervisor / "control.json", {"slack_user_id": "U1"})
    state = SlackProjector._empty_state(1_800_000_000, "U1")
    state["channel"] = "D1"
    state["projection_timestamps"]["dashboard"]["overview"] = "0.1"
    state["projection_timestamps"]["assignment"]["assignment-1"] = "0.2"
    state["projection_timestamps"]["decision"]["decision-1"] = "0.3"
    state["projection_fingerprints"]["assignment"]["assignment-1"] = "assignment-fp"
    state["projection_fingerprints"]["decision"]["decision-1"] = "decision-fp"
    write(
        supervisor / "slack-overview-state.json",
        state,
    )
    messages = {}

    def api(_token, method, payload):
        if method in {"chat.postMessage", "chat.update"}:
            ts = str(payload.get("ts") or f"1.{len(messages) + 1}")
            messages[ts] = {"ts": ts, "text": payload["text"]}
            return {"ok": True, "channel": "D1", "ts": ts}
        if method == "conversations.history":
            return {"ok": True, "messages": list(messages.values())}
        raise AssertionError(method)

    projector = SlackProjector(supervisor, api=api)
    projector.env_file = lambda: {"SLACK_BOT_TOKEN": "redacted"}
    projector.project_incidents([incident()], 1_800_000_000)
    state = json.loads((supervisor / "slack-overview-state.json").read_text())
    assert state["projection_timestamps"]["incident"]["wd-1"]
    assert state["projection_timestamps"]["dashboard"]["overview"] == "0.1"
    assert state["projection_timestamps"]["assignment"]["assignment-1"] == "0.2"
    assert state["projection_timestamps"]["decision"]["decision-1"] == "0.3"
    assert state["projection_fingerprints"]["assignment"]["assignment-1"] == "assignment-fp"
    assert state["projection_fingerprints"]["decision"]["decision-1"] == "decision-fp"
    assert state["delivery_stage"] == "Slack_message_verified"


def test_slack_acceptance_is_persisted_before_readback_and_retry_is_idempotent(
    tmp_path: Path,
):
    supervisor = tmp_path / "supervisor"
    write(supervisor / "control.json", {"slack_user_id": "U1"})
    state = SlackProjector._empty_state(1_800_000_000, "U1")
    state["channel"] = "D1"
    write(supervisor / "slack-overview-state.json", state)
    calls = []
    readback_fails = True
    messages = {}

    def api(_token, method, payload):
        nonlocal readback_fails
        calls.append(method)
        if method == "chat.postMessage":
            messages["1.1"] = {"ts": "1.1", "text": payload["text"]}
            return {"ok": True, "channel": "D1", "ts": "1.1"}
        if method == "conversations.history":
            if readback_fails:
                raise RuntimeError("readback unavailable")
            return {"ok": True, "messages": list(messages.values())}
        raise AssertionError(method)

    projector = SlackProjector(supervisor, api=api)
    projector.env_file = lambda: {"SLACK_BOT_TOKEN": "redacted"}
    with pytest.raises(RuntimeError, match="readback unavailable"):
        projector.project_incidents([incident()], 1_800_000_000)
    accepted = json.loads((supervisor / "slack-overview-state.json").read_text())
    assert accepted["delivery_stage"] == "Slack_API_accepted"
    assert accepted["projection_timestamps"]["incident"]["wd-1"] == "1.1"

    readback_fails = False
    projector.project_incidents([incident()], 1_800_000_300)
    assert calls.count("chat.postMessage") == 1
    assert "chat.update" not in calls


def test_watchdog_invokes_canonical_projector_before_additive_incidents(tmp_path: Path):
    supervisor = tmp_path / "supervisor"
    write(supervisor / "control.json", {"slack_user_id": "U1"})
    state = SlackProjector._empty_state(1_800_000_000, "U1")
    state["channel"] = "D1"
    state["ts"] = "0.1"
    state["projection_timestamps"]["assignment"]["assignment-1"] = "0.2"
    state["projection_timestamps"]["decision"]["decision-1"] = "0.3"
    write(supervisor / "slack-overview-state.json", state)
    decision_card = {"decision_id": "decision-1", "ts": "0.3"}
    write(supervisor / "decision-cards" / "decision-1.json", decision_card)
    write(
        supervisor / "slack-outbox.json",
        {"notifications": [{"current_stage": "notification_queued"}]},
    )
    calls = []

    def canonical_runner(command, **kwargs):
        calls.append((command, kwargs))
        outbox = json.loads((supervisor / "slack-outbox.json").read_text())
        outbox["notifications"][0]["current_stage"] = "Slack_message_verified"
        write(supervisor / "slack-outbox.json", outbox)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "channel": "D1",
                    "ts": "0.1",
                    "message_operation": "verified",
                }
            ),
            stderr="",
        )

    class AdditiveIncidents:
        def __init__(self):
            self.calls = []

        def project_incidents(self, incidents, at_epoch):
            self.calls.append((incidents, at_epoch))
            return []

    additive = AdditiveIncidents()
    projector = CanonicalSlackProjector(
        supervisor,
        command="canonical-projector",
        runner=canonical_runner,
        incident_projector=additive,
    )
    projector.project({}, [incident()], 1_800_000_000)
    assert calls[0][0] == ["canonical-projector"]
    assert calls[0][1]["env"]["AXIS_SUPERVISOR_MUTATION_SOURCE"] == "watchdog-projector"
    assert additive.calls == [([incident()], 1_800_000_000)]
    outbox = json.loads((supervisor / "slack-outbox.json").read_text())
    assert outbox["notifications"][0]["current_stage"] == "Slack_message_verified"
    preserved = json.loads((supervisor / "slack-overview-state.json").read_text())
    assert preserved["projection_timestamps"]["assignment"]["assignment-1"] == "0.2"
    assert preserved["projection_timestamps"]["decision"]["decision-1"] == "0.3"
    assert json.loads(
        (supervisor / "decision-cards" / "decision-1.json").read_text()
    ) == decision_card


def test_cron_and_slack_ownership_contracts_are_explicit():
    watchdog_cron = (ROOT / "scripts" / "cronctl.py").read_text()
    supervisor_cron = (SUPERVISOR_ROOT / "scripts" / "cronctl.py").read_text()
    assert 'JOB_NAME = "axis-development-watchdog"' in watchdog_cron
    assert '"schedule_display": "every 5m"' in watchdog_cron
    assert "fcntl.flock(lock, fcntl.LOCK_EX)" in watchdog_cron
    assert 'control["report_cron_job_id"] = None' in supervisor_cron
    assert '"--script", "axis-development-supervisor-slack.py"' not in supervisor_cron


def load_cronctl():
    spec = importlib.util.spec_from_file_location(
        "axis_watchdog_cronctl_test", ROOT / "scripts" / "cronctl.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cron_install_atomically_adopts_only_an_exact_orphan(tmp_path: Path, monkeypatch):
    cronctl = load_cronctl()
    cronctl.ROOT = tmp_path
    cronctl.CONTROL = tmp_path / "control.json"
    cronctl.JOBS = tmp_path / "jobs.json"
    control = json.loads((ROOT / "control.defaults.json").read_text())
    write(cronctl.CONTROL, control)
    exact = {
        "id": "orphan-id",
        "name": "axis-development-watchdog",
        "schedule_display": "every 5m",
        "script": "axis-development-watchdog.py",
        "no_agent": True,
        "enabled": False,
    }
    write(cronctl.JOBS, {"jobs": [exact]})
    calls = []
    monkeypatch.setattr(cronctl, "run", lambda hermes, *args, **kwargs: calls.append(args))
    monkeypatch.setattr(
        cronctl,
        "create",
        lambda _hermes: (_ for _ in ()).throw(AssertionError("orphan was recreated")),
    )
    assert cronctl.operate("install", "hermes") == 0
    assert json.loads(cronctl.CONTROL.read_text())["cron_job_id"] == "orphan-id"
    assert calls == [("resume", "orphan-id")]

    control["cron_job_id"] = None
    write(cronctl.CONTROL, control)
    write(cronctl.JOBS, {"jobs": [{**exact, "script": "wrong.py"}]})
    with pytest.raises(RuntimeError, match="cron drift"):
        cronctl.operate("install", "hermes")


def test_pinned_hermes_diagnostic_is_strictly_no_tool_and_read_only(monkeypatch):
    calls = []
    auxiliary = types.ModuleType("agent.auxiliary_client")

    def call_llm(**kwargs):
        calls.append(kwargs)
        return {"response": "bounded diagnosis"}

    auxiliary.call_llm = call_llm
    auxiliary.extract_content_or_reasoning = lambda response: response["response"]
    agent = types.ModuleType("agent")
    agent.__path__ = []
    monkeypatch.setitem(sys.modules, "agent", agent)
    monkeypatch.setitem(sys.modules, "agent.auxiliary_client", auxiliary)
    monkeypatch.setenv(
        "AXIS_WATCHDOG_HERMES_REVISION",
        "f5be9236e00ddf2f2a412697f267078fc4ee068e",
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO("deterministic anomaly evidence"))
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    spec = importlib.util.spec_from_file_location(
        "axis_watchdog_diagnostic_test", ROOT / "scripts" / "diagnostic_stdin.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main() == 0
    assert output.getvalue() == "bounded diagnosis\n"
    assert calls[0]["provider"] == "openai-api"
    assert calls[0]["model"] == "gpt-5.4"
    assert calls[0]["tools"] == []
    assert calls[0]["timeout"] == 90
    assert "hermes_cli.oneshot" not in (ROOT / "scripts" / "diagnostic_stdin.py").read_text()
    assert module.PINNED_HERMES_REVISION in (ROOT / "default.nix").read_text()


def test_real_recovery_levels_are_bounded_and_level4_is_home_only(tmp_path: Path):
    commands = []

    def runner(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="repaired", stderr="")

    executor = RecoveryExecutor(
        tmp_path,
        runner=runner,
        self_repair_command="repair-cron",
        runtime_repair_command="repair-runtime",
    )
    statuses = {}
    for level in range(6):
        value = {
            **incident(),
            "incident_id": f"wd-{level}",
            "recovery_level": level,
            "repair_repository": "cdenneen/home" if level == 4 else None,
        }
        statuses[level] = executor.execute(
            value,
            "no-tool diagnosis" if level == 4 else None,
            {},
            1_800_000_000,
        )[0]
    assert statuses == {
        0: "completed",
        1: "in-progress",
        2: "completed",
        3: "completed",
        4: "completed",
        5: "waiting-human",
    }
    assert commands == [["repair-cron"], ["repair-runtime"]]
    escalation = json.loads((tmp_path / "repair-escalations" / "wd-4.json").read_text())
    assert escalation["repository"] == "cdenneen/home"
    assert escalation["diagnostic_mode"] == "pinned-hermes-no-tools-read-only"
    assert escalation["product_dispatch_allowed"] is False
    schema = json.loads(
        (ROOT / "schemas" / "repair-escalation.schema.json").read_text()
    )
    jsonschema.Draft202012Validator(schema).validate(escalation)


def test_home_manager_defines_external_backup_heartbeat_timer():
    module = (ROOT / "default.nix").read_text()
    assert "systemd.user.timers.axis-development-watchdog-backup" in module
    assert 'OnUnitActiveSec = "15m"' in module
    assert "axis-development-watchdog-backup.service" in module


def test_control_schema_and_all_emitted_ledgers_are_versioned(tmp_path: Path):
    jsonschema_spec = importlib.util.find_spec("jsonschema")
    if jsonschema_spec is None:
        return
    import jsonschema

    control = json.loads((ROOT / "control.defaults.json").read_text())
    control_schema = json.loads((ROOT / "schemas" / "control.schema.json").read_text())
    jsonschema.Draft202012Validator(control_schema).validate(control)

    root, supervisor, jobs = setup_runtime(tmp_path)
    Watchdog(
        root,
        supervisor,
        jobs,
        clock=lambda: 1_800_000_000,
        projector=FakeProjector(fail=True),
        diagnostic=FakeDiagnostic(),
    ).run()
    names = {
        "state.json": "state.schema.json",
        "states.jsonl": "state.schema.json",
        "heartbeat.json": "heartbeat.schema.json",
        "observations.jsonl": "observation.schema.json",
        "incidents.jsonl": "incident.schema.json",
        "recoveries.jsonl": "recovery.schema.json",
        "projections.jsonl": "projection.schema.json",
    }
    for record_name, schema_name in names.items():
        schema = json.loads((ROOT / "schemas" / schema_name).read_text())
        path = root / record_name
        records = (
            [json.loads(line) for line in path.read_text().splitlines()]
            if path.suffix == ".jsonl"
            else [json.loads(path.read_text())]
        )
        for record in records:
            jsonschema.Draft202012Validator(schema).validate(record)
