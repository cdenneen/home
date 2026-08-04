import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def control(**overrides):
    value = {
        "schema": "axis.external-development-supervisor.control",
        "schema_version": "1.0.0",
        "mode": "enabled",
        "kill_switch": False,
        "allow_repository_mutation": False,
        "repository_allowlist": ["ghostspace/axis"],
    }
    value.update(overrides)
    return value


def test_authority_requires_exact_approval_digest():
    reconcile = load_module("reconcile", ROOT / "scripts" / "reconcile.py")
    record = "planning_record:\n  digest: sha256:" + "a" * 64
    matching = ["Product Owner approval — Approve exact digest sha256:" + "a" * 64]
    mismatch = ["Product Owner approval — Approve exact digest sha256:" + "b" * 64]
    assert reconcile.authority_from_text(record, matching, matching)["approval_matches_record"]
    assert reconcile.authority_from_text(record, mismatch, mismatch)["approval_mismatch"]
    assert not reconcile.authority_from_text("", matching, matching)["approval_matches_record"]


def test_ready_label_does_not_bypass_authority():
    reconcile = load_module("reconcile_ready", ROOT / "scripts" / "reconcile.py")
    issue = {"state": "opened", "labels": ["ready"], "title": "Ungoverned"}
    classification, blocker, _ = reconcile.classify_issue(issue, "", [], [], [], [])
    assert classification == "Waiting"
    assert blocker == "governance"


def test_waiting_decomposition_is_recorded():
    reconcile = load_module("reconcile_decomp", ROOT / "scripts" / "reconcile.py")
    value = reconcile.decomposition_for(
        "acceptance_id: AC-1\nstate: open\nstatement: bounded slice",
        "Waiting",
        "Dependency",
    )
    assert value["evaluated"] is True
    assert value["open_acceptance_ids"] == ["AC-1"]


def test_paginated_gitlab_arrays_are_fully_decoded():
    reconcile = load_module("reconcile_pages", ROOT / "scripts" / "reconcile.py")
    assert reconcile.decode_json_stream('[{"id":1}]\n[{"id":2}]\n') == [
        {"id": 1},
        {"id": 2},
    ]


def test_kill_switch_suppresses_before_reconciliation(tmp_path: Path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "control.json").write_text(
        json.dumps(control(kill_switch=True)), encoding="utf-8"
    )
    env = os.environ | {"AXIS_SUPERVISOR_ROOT": str(root)}
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "preflight.py")],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["wakeAgent"] is False
    assert not (root / "inventory.lock").exists()


def test_daily_budget_suppresses_before_reconciliation(tmp_path: Path):
    root = tmp_path / "runtime"
    (root / "runs").mkdir(parents=True)
    (root / "assignments").mkdir()
    (root / "control.json").write_text(
        json.dumps(control(minimum_free_disk_gib=0, daily_worker_cycle_limit=0)),
        encoding="utf-8",
    )
    env = os.environ | {"AXIS_SUPERVISOR_ROOT": str(root)}
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "preflight.py")],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["wakeAgent"] is False
    assert "daily model call limit" in payload["reason"]


def test_reconciliation_failure_suppresses_agent(tmp_path: Path):
    root = tmp_path / "runtime"
    (root / "runs").mkdir(parents=True)
    (root / "assignments").mkdir()
    (root / "control.json").write_text(
        json.dumps(control(minimum_free_disk_gib=0, daily_worker_cycle_limit=99)),
        encoding="utf-8",
    )
    failing = tmp_path / "fail.py"
    failing.write_text("raise SystemExit(2)\n", encoding="utf-8")
    env = os.environ | {
        "AXIS_SUPERVISOR_ROOT": str(root),
        "AXIS_SUPERVISOR_RECONCILE": str(failing),
        "AXIS_SUPERVISOR_CTL": str(ROOT / "scripts" / "supervisorctl.py"),
    }
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "preflight.py")],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["wakeAgent"] is False
    assert "live reconciliation failed closed" in payload["reason"]


def test_fenced_lease_conflict_and_release(tmp_path: Path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "control.json").write_text(
        json.dumps(
            control(
                allow_repository_mutation=True,
                max_active_assignments=1,
                lease_seconds=120,
            )
        ),
        encoding="utf-8",
    )
    env = os.environ | {"AXIS_SUPERVISOR_ROOT": str(root)}
    script = str(ROOT / "scripts" / "supervisorctl.py")
    first = subprocess.run(
        [sys.executable, script, "claim", "a1", "--run-id", "r1", "--resource", "path:ghostspace/axis:src"],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    lease = json.loads(first.stdout)
    conflict = subprocess.run(
        [sys.executable, script, "claim", "a2", "--run-id", "r2", "--resource", "path:ghostspace/axis:src"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert conflict.returncode != 0
    subprocess.run(
        [sys.executable, script, "release", "a1", "--token", lease["fencing_token"]],
        env=env,
        check=True,
    )
    assert not (root / "leases" / "a1").exists()


def test_expired_lease_recovery(tmp_path: Path):
    root = tmp_path / "runtime"
    lease_dir = root / "leases" / "expired"
    lease_dir.mkdir(parents=True)
    (root / "control.json").write_text(json.dumps(control()), encoding="utf-8")
    (lease_dir / "lease.json").write_text(
        json.dumps(
            {
                "assignment_id": "expired",
                "fencing_token": "token-token-token-token",
                "expires_at_epoch": 1,
            }
        ),
        encoding="utf-8",
    )
    env = os.environ | {"AXIS_SUPERVISOR_ROOT": str(root)}
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "supervisorctl.py"), "recover"],
        env=env,
        check=True,
    )
    assert lease_dir.exists()
    recovered = json.loads((lease_dir / "lease.json").read_text(encoding="utf-8"))
    assert recovered["recovery_required"] is True
    heartbeat = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "supervisorctl.py"),
            "heartbeat",
            "expired",
            "--token",
            "token-token-token-token",
        ],
        env=env,
        check=False,
    )
    assert heartbeat.returncode != 0


def test_resource_allowlist_requires_exact_project(tmp_path: Path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "control.json").write_text(
        json.dumps(control(allow_repository_mutation=True, max_active_assignments=1)),
        encoding="utf-8",
    )
    env = os.environ | {"AXIS_SUPERVISOR_ROOT": str(root)}
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "supervisorctl.py"),
            "claim",
            "bad",
            "--run-id",
            "r1",
            "--resource",
            "path:evil/ghostspace/axis:src",
        ],
        env=env,
        check=False,
    )
    assert result.returncode != 0


def test_concurrent_claims_are_serialized(tmp_path: Path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "control.json").write_text(
        json.dumps(
            control(
                allow_repository_mutation=True,
                max_active_assignments=1,
                lease_seconds=120,
            )
        ),
        encoding="utf-8",
    )
    env = os.environ | {"AXIS_SUPERVISOR_ROOT": str(root)}
    script = str(ROOT / "scripts" / "supervisorctl.py")
    commands = [
        [sys.executable, script, "claim", assignment, "--run-id", assignment, "--resource", "path:ghostspace/axis:src"]
        for assignment in ("a1", "a2")
    ]
    processes = [subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for command in commands]
    returncodes = [process.wait(timeout=10) for process in processes]
    assert sorted(returncodes) == [0, 1]


def test_reporter_rejects_inconsistent_queue(tmp_path: Path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "control.json").write_text(json.dumps(control()), encoding="utf-8")
    (root / "inventory.json").write_text(
        json.dumps(
            {
                "schema": "axis.external-development-supervisor.inventory",
                "schema_version": "1.0.0",
                "classification_counts": {"Executable": 0, "Unknown": 0},
                "waiting_reason_counts": {"Unknown": 0},
                "queue_depth": 0,
                "executable_queue": [{"ref": "bad"}],
                "invariant": {"unknown_count": 0},
            }
        ),
        encoding="utf-8",
    )
    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps({"jobs": []}), encoding="utf-8")
    env = os.environ | {
        "AXIS_SUPERVISOR_ROOT": str(root),
        "AXIS_SUPERVISOR_CRON_JOBS": str(jobs),
    }
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "report.py")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "queue_depth" in result.stdout


def test_reporter_suppresses_during_inventory_generation(tmp_path: Path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "inventory.lock").mkdir()
    env = os.environ | {"AXIS_SUPERVISOR_ROOT": str(root)}
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "report.py")],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip() == "[SILENT]"


def test_report_delivery_is_acknowledged_on_next_successful_run(tmp_path: Path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "control.json").write_text(
        json.dumps(control(report_cron_job_id="report", report_heartbeat_minutes=90)),
        encoding="utf-8",
    )
    counts = {
        name: 0
        for name in (
            "Executable",
            "Running",
            "Blocked",
            "Waiting",
            "Integrated",
            "Superseded",
            "Completed",
            "Invalid",
            "Unknown",
        )
    }
    inventory = {
        "schema": "axis.external-development-supervisor.inventory",
        "schema_version": "1.0.0",
        "generation_id": "g1",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "classification_counts": counts,
        "waiting_reason_counts": {"Unknown": 0},
        "queue_depth": 0,
        "executable_queue": [],
        "invariant": {"unknown_count": 0},
        "idle_proof": {"queue_zero_proven": True},
        "work_items": [],
        "repositories": {},
    }
    (root / "inventory.json").write_text(json.dumps(inventory), encoding="utf-8")
    jobs = tmp_path / "jobs.json"
    jobs.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "id": "report",
                        "repeat": {"completed": 0},
                        "last_status": None,
                        "last_delivery_error": None,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    env = os.environ | {
        "AXIS_SUPERVISOR_ROOT": str(root),
        "AXIS_SUPERVISOR_CRON_JOBS": str(jobs),
    }
    script = str(ROOT / "scripts" / "report.py")
    first = subprocess.run(
        [sys.executable, script], env=env, text=True, capture_output=True, check=True
    )
    assert "AXIS Development Supervisor" in first.stdout
    assert (root / "report-delivery-pending.json").exists()
    jobs.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "id": "report",
                        "repeat": {"completed": 1},
                        "last_status": "ok",
                        "last_delivery_error": None,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    second = subprocess.run(
        [sys.executable, script], env=env, text=True, capture_output=True, check=True
    )
    assert second.stdout.strip() == "[SILENT]"
    assert (root / "report-delivery-state.json").exists()
    assert not (root / "report-delivery-pending.json").exists()
