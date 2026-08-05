import json
import os
import re
import signal
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import quote

from .decomposition import SemanticDecompositionEngine
from .prompt_factory import PromptFactory
from .integrator import Integrator


class HermesWorkerManager:
    def __init__(self, root: Path, hermes: str, supervisorctl: str):
        self.root = root
        self.hermes = hermes
        self.supervisorctl = supervisorctl
        self.prompts = PromptFactory()
        self.decomposition = SemanticDecompositionEngine(root)

    def hermes_python(self) -> str:
        launcher = self.hermes
        if not Path(launcher).is_absolute():
            launcher = shutil.which(launcher) or launcher
        wrapper = Path(launcher).resolve()
        try:
            text = wrapper.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise RuntimeError(f"cannot inspect Hermes launcher: {wrapper}") from exc
        match = re.search(r"export HERMES_PYTHON='([^']+)'", text)
        if not match or not Path(match.group(1)).is_file():
            raise RuntimeError("Hermes launcher does not declare a valid HERMES_PYTHON")
        return match.group(1)

    def record_model_attempt(self, assignment: dict, model: str) -> None:
        now = int(time.time())
        assignment["model_attempts"] = int(assignment.get("model_attempts") or 0) + 1
        assignment.setdefault("model_attempt_log", []).append(
            {"started_at_epoch": now, "model": model}
        )
        path = self.root / "assignments" / f"{assignment['assignment_id']}.json"
        if not path.exists():
            return
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(assignment, indent=2) + "\n", encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(path)

    @staticmethod
    def extract_json(text: str) -> dict:
        decoder = json.JSONDecoder()
        for match in re.finditer(r"\{", text):
            try:
                value, _ = decoder.raw_decode(text[match.start() :])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        raise ValueError("worker output contained no JSON object")

    def run_model(
        self,
        model: str,
        prompt: str,
        timeout: int,
        assignment: dict,
        toolsets: str | None = None,
    ) -> str:
        control = json.loads((self.root / "control.json").read_text(encoding="utf-8"))
        prompt_bytes = len(prompt.encode("utf-8"))
        maximum_prompt_bytes = int(control.get("max_semantic_prompt_bytes", 200_000))
        if prompt_bytes > maximum_prompt_bytes:
            raise ValueError(
                f"worker prompt exceeds bounded size: {prompt_bytes}/{maximum_prompt_bytes} bytes"
            )
        self.record_model_attempt(assignment, model)
        stop_heartbeat = threading.Event()
        lease = assignment.get("lease") or {}

        def heartbeat() -> None:
            while not stop_heartbeat.wait(300):
                subprocess.run(
                    [
                        "python3",
                        self.supervisorctl,
                        "heartbeat",
                        assignment["assignment_id"],
                        "--token",
                        str(lease.get("fencing_token")),
                    ],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        heartbeat_thread.start()
        command = [
            self.hermes_python(),
            str(Path(__file__).with_name("oneshot_stdin.py")),
            "--provider",
            "openai-api",
            "--model",
            model,
            "--reasoning",
            "medium",
        ]
        if toolsets is not None:
            command.extend(["--toolsets", toolsets])
        process = subprocess.Popen(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=assignment.get("worktree") or str(self.root),
        )
        try:
            output, _ = process.communicate(input=prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                output, _ = process.communicate(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                output, _ = process.communicate()
            raise TimeoutError(f"worker timed out: {output[-2000:]}")
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join(timeout=5)
        if process.returncode != 0:
            raise RuntimeError(f"worker failed ({process.returncode}): {output[-4000:]}")
        return output

    def semantic(self, assignment: dict) -> dict:
        target = str(assignment.get("target_ref") or "")
        match = re.fullmatch(r"([^#]+)#(\d+)", target)
        if match:
            project, iid = match.groups()
            encoded = quote(project, safe="")

            def glab_json(path: str):
                output = subprocess.check_output(
                    [
                        "/etc/profiles/per-user/cdenneen/bin/glab",
                        "api",
                        "--hostname",
                        "gitlab.com",
                        path,
                    ],
                    text=True,
                    timeout=90,
                )
                return json.loads(output)

            issue = glab_json(f"projects/{encoded}/issues/{iid}")
            raw_notes = glab_json(f"projects/{encoded}/issues/{iid}/notes?per_page=100")
            raw_events = glab_json(
                f"projects/{encoded}/issues/{iid}/resource_state_events?per_page=100"
            )
            notes = [
                {
                    "id": note.get("id"),
                    "author": (note.get("author") or {}).get("username"),
                    "created_at": note.get("created_at"),
                    "body": str(note.get("body") or "")[:4000],
                }
                for note in raw_notes[:20]
            ]
            events = [
                {
                    "id": event.get("id"),
                    "state": event.get("state"),
                    "created_at": event.get("created_at"),
                    "user": (event.get("user") or {}).get("username"),
                }
                for event in raw_events[:100]
            ]
            repo = Path("/home/cdenneen/src/workspace/personal/work") / project.split("/")[-1]
            subprocess.run(
                ["git", "fetch", "--prune", "origin"], cwd=repo, check=True, timeout=120
            )
            grep = subprocess.run(
                ["git", "grep", "-n", target, "origin/main", "--", "."],
                cwd=repo,
                text=True,
                capture_output=True,
                timeout=60,
            )
            history = subprocess.run(
                ["git", "log", "--all", "--oneline", "--grep", target, "-20"],
                cwd=repo,
                text=True,
                capture_output=True,
                timeout=60,
            )
            assignment["semantic_evidence"] = {
                "issue": {
                    key: issue.get(key)
                    for key in (
                        "id",
                        "iid",
                        "title",
                        "state",
                        "created_at",
                        "updated_at",
                        "closed_at",
                        "labels",
                        "milestone",
                        "web_url",
                        "task_completion_status",
                    )
                }
                | {"description": str(issue.get("description") or "")[:12000]},
                "notes": notes,
                "resource_state_events": events,
                "origin_main": subprocess.check_output(
                    ["git", "rev-parse", "origin/main"], cwd=repo, text=True
                ).strip(),
                "repository_references": grep.stdout.splitlines()[:100],
                "commit_references": history.stdout.splitlines(),
                "parents": [],
                "merge_requests": [],
            }
            if assignment.get("technical_results"):
                assignment["semantic_evidence"]["technical_results"] = assignment[
                    "technical_results"
                ]
            source_item = assignment.get("source_item") or {}
            if issue.get("updated_at") != source_item.get("updated_at"):
                raise RuntimeError("target issue changed after assignment creation")
            if assignment["semantic_evidence"]["origin_main"] != source_item.get(
                "repository_head"
            ):
                raise RuntimeError("canonical main changed after assignment creation")
            parent_refs = sorted(
                set(
                    (source_item.get("dependencies") or [])
                    + ((source_item.get("source_evidence") or {}).get("parent_refs") or [])
                )
            )
            for parent_ref in parent_refs:
                parent_match = re.fullmatch(r"([^#]+)#(\d+)", parent_ref)
                if not parent_match:
                    continue
                parent_project, parent_iid = parent_match.groups()
                parent_encoded = quote(parent_project, safe="")
                assignment["semantic_evidence"]["parents"].append(
                    {
                        "ref": parent_ref,
                        "issue": glab_json(
                            f"projects/{parent_encoded}/issues/{parent_iid}"
                        ),
                        "notes": glab_json(
                            f"projects/{parent_encoded}/issues/{parent_iid}/notes?per_page=100"
                        ),
                    }
                )
            for related in source_item.get("merge_requests") or []:
                mr_iid = int(related.get("iid") or 0)
                if not mr_iid:
                    continue
                mr = glab_json(f"projects/{encoded}/merge_requests/{mr_iid}")
                pipeline = mr.get("head_pipeline") or {}
                pipeline_id = int(pipeline.get("id") or 0)
                pipeline_detail = (
                    glab_json(f"projects/{encoded}/pipelines/{pipeline_id}")
                    if pipeline_id
                    else None
                )
                jobs = (
                    glab_json(f"projects/{encoded}/pipelines/{pipeline_id}/jobs?per_page=100")
                    if pipeline_id
                    else []
                )
                merge_commit = mr.get("merge_commit_sha")
                ancestor = False
                if merge_commit:
                    ancestor = subprocess.run(
                        ["git", "merge-base", "--is-ancestor", merge_commit, "origin/main"],
                        cwd=repo,
                        timeout=30,
                    ).returncode == 0
                source_branch = str(mr.get("source_branch") or "")
                remote_branch_present = bool(
                    source_branch
                    and subprocess.run(
                        ["git", "ls-remote", "--exit-code", "--heads", "origin", source_branch],
                        cwd=repo,
                        text=True,
                        capture_output=True,
                        timeout=60,
                    ).returncode
                    == 0
                )
                local_branches = subprocess.check_output(
                    ["git", "branch", "--format=%(refname:short)"], cwd=repo, text=True
                ).splitlines()
                worktrees = subprocess.check_output(
                    ["git", "worktree", "list", "--porcelain"], cwd=repo, text=True
                )
                assignment["semantic_evidence"]["merge_requests"].append(
                    {
                        "merge_request": {
                            key: mr.get(key)
                            for key in (
                                "iid",
                                "state",
                                "title",
                                "source_branch",
                                "target_branch",
                                "sha",
                                "merge_commit_sha",
                                "merged_at",
                                "web_url",
                                "detailed_merge_status",
                            )
                        },
                        "pipeline": {
                            key: (pipeline_detail or {}).get(key)
                            for key in (
                                "id",
                                "status",
                                "sha",
                                "ref",
                                "created_at",
                                "finished_at",
                                "web_url",
                            )
                        }
                        if pipeline_detail
                        else None,
                        "jobs": [
                            {
                                key: job.get(key)
                                for key in (
                                    "id",
                                    "name",
                                    "stage",
                                    "status",
                                    "web_url",
                                    "artifacts_file",
                                )
                            }
                            for job in jobs
                        ],
                        "merge_commit_on_current_main": ancestor,
                        "cleanup": {
                            "remote_source_branch_absent": not remote_branch_present,
                            "local_source_branch_absent": source_branch not in local_branches,
                            "source_branch_worktree_absent": source_branch not in worktrees,
                        },
                    }
                )
            assignment["evidence_fingerprint"] = self.decomposition.save_evidence(
                assignment["target_ref"], assignment["semantic_evidence"]
            )
        output = self.run_model(
            "gpt-5.4",
            self.prompts.semantic_prompt(assignment),
            900,
            assignment,
            toolsets="",
        )
        record = self.extract_json(output)
        if assignment.get("revalidation_tier") == "A":
            verification = record.get("verification_result") or {}
            if verification.get("tier") != "A":
                raise ValueError("Tier A worker did not return a Tier A verification result")
            if verification.get("disposition") == "active-technical-revalidation":
                candidates = record.get("candidate_slices") or []
                if not any(
                    candidate.get("result") == "Executable"
                    and candidate.get("category")
                    in {"audit", "tests", "fixtures", "benchmark", "negative-test"}
                    and candidate.get("required_tests")
                    for candidate in candidates
                ):
                    raise ValueError(
                        "Tier A technical disposition requires a bounded Tier B test candidate"
                    )
        path = self.decomposition.save(record)
        return {"record": record, "path": str(path), "raw_output": output[-4000:]}

    def technical_revalidation(self, assignment: dict) -> dict:
        repo = Path("/home/cdenneen/src/workspace/personal/work") / assignment[
            "project"
        ].split("/")[-1]
        worktree = self.root / "worktrees" / assignment["assignment_id"]
        subprocess.run(["git", "fetch", "--prune", "origin"], cwd=repo, check=True, timeout=120)
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree), "origin/main"],
            cwd=repo,
            check=True,
            timeout=120,
        )
        results = []
        try:
            from .models import test_command_argv

            for command in assignment.get("required_tests") or []:
                completed = subprocess.run(
                    test_command_argv(command),
                    cwd=worktree,
                    text=True,
                    capture_output=True,
                    timeout=600,
                )
                results.append(
                    {
                        "command": command,
                        "returncode": completed.returncode,
                        "stdout_tail": completed.stdout[-2000:],
                        "stderr_tail": completed.stderr[-2000:],
                    }
                )
                if completed.returncode != 0:
                    break
            assignment["technical_results"] = {
                "main_sha": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=worktree, text=True
                ).strip(),
                "tests": results,
                "all_passed": bool(results)
                and all(result["returncode"] == 0 for result in results),
            }
            return self.semantic(assignment)
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=repo,
                check=False,
            )

    def implementation(self, assignment: dict, repo: Path) -> dict:
        branch = f"hermes/{assignment['assignment_id']}"
        worktree = self.root / "worktrees" / assignment["assignment_id"]
        subprocess.run(["git", "fetch", "--prune", "origin"], cwd=repo, check=True, timeout=120)
        subprocess.run(
            ["git", "worktree", "add", "-b", branch, str(worktree), "origin/main"],
            cwd=repo,
            check=True,
            timeout=120,
        )
        prompt = self.prompts.implementation_prompt(assignment, str(worktree))
        assignment["worktree"] = str(worktree)
        output = self.run_model("gpt-5.3-codex", prompt, 1800, assignment)
        changed = subprocess.check_output(
            ["git", "diff", "--name-only", "origin/main...HEAD"],
            cwd=worktree,
            text=True,
            timeout=60,
        ).splitlines()
        allowed = set(assignment.get("allowed_paths") or [])
        disallowed = [path for path in changed if path not in allowed]
        if disallowed:
            raise RuntimeError(f"worker changed paths outside assignment: {disallowed}")
        handoff = self.extract_json(output)
        status_paths = []
        for line in subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=worktree, text=True, timeout=60
        ).splitlines():
            if len(line) >= 4:
                status_paths.append(line[3:].split(" -> ")[-1])
        disallowed_status = [path for path in status_paths if path not in allowed]
        if disallowed_status:
            raise RuntimeError(
                f"worker left changes outside assignment: {disallowed_status}"
            )
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=worktree, text=True, timeout=30
        ).strip()
        if handoff.get("commit") != head:
            raise RuntimeError("worker handoff commit does not match worktree HEAD")
        return {
            "branch": branch,
            "worktree": str(worktree),
            "changed_paths": changed,
            "commit": head,
            "handoff": handoff,
            "raw_output": output[-4000:],
        }

    def integration(self, assignment: dict, glab: str) -> dict:
        worker = assignment.get("worker") or {}
        handoff = worker.get("handoff") or {}
        iid = int(handoff.get("mr_iid") or 0)
        if not iid:
            raise ValueError("implementation handoff has no MR iid")
        inspection = Integrator(glab).inspect_mr(assignment["project"], iid)
        prompt = self.prompts.integration_prompt(assignment, inspection)
        assignment["worktree"] = worker.get("worktree")
        output = self.run_model("gpt-5.4", prompt, 1200, assignment)
        return {"result": self.extract_json(output), "raw_output": output[-4000:]}
