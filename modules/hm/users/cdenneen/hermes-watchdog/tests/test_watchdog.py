import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

from axis_watchdog.engine import Watchdog
from axis_watchdog.projection import SlackProjector

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
            "primary_kpi": {"count": 1, "denominator": 2},
            "capabilities": [
                {
                    "capability": "CLI",
                    "graduated": True,
                    "production_confidence": 100,
                    "first_failing_gate": None,
                },
                {
                    "capability": "Web",
                    "graduated": False,
                    "production_confidence": 50,
                    "first_failing_gate": "verification",
                },
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
    assert len(diagnostic.calls) == 1

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
    recoveries = [json.loads(line) for line in (root / "recoveries.jsonl").read_text().splitlines()]
    assert {item["action"] for item in recoveries} >= {
        "observe-and-catch-up",
        "deterministic-health-restored",
    }


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
    recovery = [json.loads(line) for line in (root / "recoveries.jsonl").read_text().splitlines()][-1]
    assert recovery["level"] == 4
    assert recovery["target"] == "cdenneen/home"
    assert recovery["action"] == "escalate-supervisor-repair"

    mission = json.loads((supervisor / "active-mission.json").read_text())
    mission["graduation_progress"]["graduated"] = 2
    write(supervisor / "active-mission.json", mission)
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
    assert len(diagnostic.calls) == 1
    assert supervisor_digest(supervisor) == before
    recoveries = [json.loads(line) for line in (root / "recoveries.jsonl").read_text().splitlines()]
    assert all(item["target"] in {"axis-development-watchdog", "cdenneen/home"} for item in recoveries)
    assert not any("ghostspace/axis" in json.dumps(item) for item in recoveries)


def test_slack_projection_owns_existing_dashboard_and_incident_mappings(tmp_path: Path):
    supervisor = tmp_path / "supervisor"
    write(supervisor / "control.json", {"slack_user_id": "U1"})
    write(
        supervisor / "slack-overview-state.json",
        SlackProjector._empty_state(1_800_000_000, "U1"),
    )
    messages = {}

    def api(_token, method, payload):
        if method == "auth.test":
            return {"ok": True, "team_id": "T1", "team": "Test", "user_id": "UBOT"}
        if method == "conversations.open":
            return {"ok": True, "channel": {"id": "D1"}}
        if method in {"chat.postMessage", "chat.update"}:
            ts = str(payload.get("ts") or f"1.{len(messages) + 1}")
            messages[ts] = {"ts": ts, "text": payload["text"]}
            return {"ok": True, "channel": "D1", "ts": ts}
        if method == "conversations.history":
            return {"ok": True, "messages": list(messages.values())}
        raise AssertionError(method)

    projector = SlackProjector(supervisor, api=api)
    projector.env_file = lambda: {"SLACK_BOT_TOKEN": "redacted"}
    report = {
        "summary": {
            "overall": "degraded",
            "mission": "active",
            "capabilities": "1/2 graduated",
            "open_incidents": 1,
        },
        "sections": [("AXIS", "Mission active"), ("WATCHDOG", "Liveness healthy")],
    }
    incident = {
        "incident_id": "wd-1",
        "status": "recovering",
        "anomaly_code": "test-anomaly",
        "summary": "test anomaly",
        "recovery_level": 1,
        "repair_repository": None,
        "observed_at": iso(1_800_000_000),
        "diagnosis": None,
    }
    projector.project(report, [incident], 1_800_000_000)
    state = json.loads((supervisor / "slack-overview-state.json").read_text())
    assert state["projection_timestamps"]["dashboard"]["overview"]
    assert state["projection_timestamps"]["incident"]["wd-1"]
    assert state["delivery_stage"] == "Slack_message_verified"
    assert state["dashboard_fallback"]["blocks"][0]["text"]["text"] == "AXIS"


def test_cron_and_slack_ownership_contracts_are_explicit():
    watchdog_cron = (ROOT / "scripts" / "cronctl.py").read_text()
    supervisor_cron = (SUPERVISOR_ROOT / "scripts" / "cronctl.py").read_text()
    assert 'JOB_NAME = "axis-development-watchdog"' in watchdog_cron
    assert '"--no-agent",\n            "every 5m"' in watchdog_cron
    assert 'control["report_cron_job_id"] = None' in supervisor_cron
    assert '"--script", "axis-development-supervisor-slack.py"' not in supervisor_cron


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
