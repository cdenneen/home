import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKER_PROMPT = "reviewed legacy worker prompt"


def load_module():
    spec = importlib.util.spec_from_file_location("decommission_cron_test", ROOT / "scripts" / "decommission_cron.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def fixture(home: Path, suffix: str = ""):
    worker = {
        "id": f"worker{suffix}",
        "name": "axis-development-supervisor-worker",
        "schedule_display": "every 10m",
        "script": "axis-development-supervisor-preflight.py",
        "skill": "axis-development-supervisor",
        "workdir": "/home/cdenneen/src/workspace/personal/work",
        "provider": "openai-api",
        "model": "gpt-5.4",
        "no_agent": False,
        "prompt": WORKER_PROMPT,
    }
    report = {
        "id": f"report{suffix}",
        "name": "axis-development-supervisor-report",
        "schedule_display": "every 5m",
        "script": "axis-development-supervisor-slack.py",
        "no_agent": True,
    }
    watchdog = {
        "id": f"watchdog{suffix}",
        "name": "axis-development-watchdog",
        "schedule_display": "every 5m",
        "script": "axis-development-watchdog.py",
        "no_agent": True,
    }
    generic = {"id": f"generic{suffix}", "name": "generic-hermes-job", "schedule_display": "every 1h"}
    write(home / "cron/jobs.json", {"jobs": [worker, report, watchdog, generic]})
    write(
        home / "supervisor/axis-development-supervisor/control.json",
        {"schema": "axis.external-development-supervisor.control", "cron_generation": 1, "cron_job_id": worker["id"], "report_cron_job_id": report["id"]},
    )
    write(
        home / "supervisor/axis-development-watchdog/control.json",
        {"schema": "axis.development-watchdog.control", "cron_generation": 1, "cron_job_id": watchdog["id"]},
    )
    return generic


def fake_hermes(path: Path):
    path.write_text(
        f"""#!{sys.executable}
import json, os, pathlib, sys
jobs = pathlib.Path(os.environ['HERMES_HOME']) / 'cron/jobs.json'
value = json.loads(jobs.read_text())
if sys.argv[1:3] == ['cron', 'remove']:
    value['jobs'] = [job for job in value['jobs'] if str(job.get('id')) != sys.argv[3]]
    jobs.write_text(json.dumps(value))
""",
        encoding="utf-8",
    )
    path.chmod(0o700)


def test_decommission_removes_only_exact_owned_jobs_in_unprofiled_and_profile_state(tmp_path: Path):
    module = load_module()
    generic_root = fixture(tmp_path, "-root")
    generic_profile = fixture(tmp_path / "profiles/nyx-gitlab", "-profile")
    hermes = tmp_path / "hermes"
    fake_hermes(hermes)

    removed = module.apply(tmp_path, str(hermes), WORKER_PROMPT)

    assert {item["id"] for item in removed} == {
        "worker-root", "report-root", "watchdog-root",
        "worker-profile", "report-profile", "watchdog-profile",
    }
    assert json.loads((tmp_path / "cron/jobs.json").read_text())["jobs"] == [generic_root]
    assert json.loads((tmp_path / "profiles/nyx-gitlab/cron/jobs.json").read_text())["jobs"] == [generic_profile]
    assert json.loads((tmp_path / "supervisor/axis-development-supervisor/control.json").read_text())["cron_job_id"] is None
    assert json.loads((tmp_path / "profiles/nyx-gitlab/supervisor/axis-development-watchdog/control.json").read_text())["cron_job_id"] is None
    assert module.apply(tmp_path, str(hermes), WORKER_PROMPT) == []


def test_decommission_fails_closed_on_contract_drift_or_unowned_id(tmp_path: Path):
    module = load_module()
    fixture(tmp_path)
    jobs_path = tmp_path / "cron/jobs.json"
    value = json.loads(jobs_path.read_text())
    value["jobs"][0]["schedule_display"] = "every 1m"
    write(jobs_path, value)
    with pytest.raises(RuntimeError, match="contract drift"):
        module.plan_home(tmp_path, WORKER_PROMPT)

    value["jobs"][0]["schedule_display"] = "every 10m"
    write(jobs_path, value)
    control_path = tmp_path / "supervisor/axis-development-supervisor/control.json"
    control = json.loads(control_path.read_text())
    control["cron_job_id"] = "someone-else"
    write(control_path, control)
    with pytest.raises(RuntimeError, match="ownership ID"):
        module.plan_home(tmp_path, WORKER_PROMPT)


def test_decommission_plans_every_home_before_mutation(tmp_path: Path):
    module = load_module()
    fixture(tmp_path, "-root")
    profile = tmp_path / "profiles/nyx-gitlab"
    fixture(profile, "-profile")
    profile_jobs = json.loads((profile / "cron/jobs.json").read_text())
    profile_jobs["jobs"][0]["schedule_display"] = "every 1m"
    write(profile / "cron/jobs.json", profile_jobs)
    root_before = (tmp_path / "cron/jobs.json").read_text()
    root_control_before = (tmp_path / "supervisor/axis-development-supervisor/control.json").read_text()
    hermes = tmp_path / "hermes"
    fake_hermes(hermes)

    with pytest.raises(RuntimeError, match="contract drift"):
        module.apply(tmp_path, str(hermes), WORKER_PROMPT)

    assert (tmp_path / "cron/jobs.json").read_text() == root_before
    assert (tmp_path / "supervisor/axis-development-supervisor/control.json").read_text() == root_control_before


def test_decommission_rejects_prompt_and_generation_drift(tmp_path: Path):
    module = load_module()
    fixture(tmp_path)
    jobs_path = tmp_path / "cron/jobs.json"
    value = json.loads(jobs_path.read_text())
    value["jobs"][0]["prompt"] = "changed"
    write(jobs_path, value)
    with pytest.raises(RuntimeError, match="prompt"):
        module.plan_home(tmp_path, WORKER_PROMPT)

    value["jobs"][0]["prompt"] = WORKER_PROMPT
    write(jobs_path, value)
    control_path = tmp_path / "supervisor/axis-development-supervisor/control.json"
    control = json.loads(control_path.read_text())
    control["cron_generation"] = 999
    write(control_path, control)
    with pytest.raises(RuntimeError, match="cron_generation"):
        module.plan_home(tmp_path, WORKER_PROMPT)

    control["cron_generation"] = 1
    write(control_path, control)
    watchdog_control_path = tmp_path / "supervisor/axis-development-watchdog/control.json"
    watchdog_control = json.loads(watchdog_control_path.read_text())
    watchdog_control["cron_generation"] = 999
    write(watchdog_control_path, watchdog_control)
    with pytest.raises(RuntimeError, match="cron_generation"):
        module.plan_home(tmp_path, WORKER_PROMPT)
