import json
import os
import re
import signal
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import quote

from .accounting import AccountingLedger
from .decomposition import SemanticDecompositionEngine
from .models import test_command_argv, validate_allowed_path
from .mutation import GateDecision, MutationGate, OperationClass, load_canonical_lease
from .prompt_factory import PromptFactory
from .schema_registry import read_record


def resolve_allowed_source(worktree: Path, relative: str) -> Path:
    relative = validate_allowed_path(relative)
    path = worktree / relative
    resolved = path.resolve()
    if not resolved.is_relative_to(worktree.resolve()) or path.is_symlink():
        raise RuntimeError(f"allowlisted path escapes repository custody: {relative}")
    return path


def run_isolated_test(worktree: Path, command: str) -> subprocess.CompletedProcess:
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise RuntimeError("bubblewrap is required for isolated supervisor tests")
    return subprocess.run(
        [
            bwrap,
            "--die-with-parent",
            "--unshare-net",
            "--clearenv",
            "--ro-bind",
            "/nix",
            "/nix",
            "--ro-bind",
            "/etc",
            "/etc",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/workspace",
            "--bind",
            str(worktree),
            "/workspace",
            "--ro-bind",
            str(worktree / ".git"),
            "/workspace/.git",
            "--setenv",
            "HOME",
            "/tmp",
            "--setenv",
            "PATH",
            "/etc/profiles/per-user/cdenneen/bin:/run/current-system/sw/bin",
            "--setenv",
            "LANG",
            "C.UTF-8",
            "--chdir",
            "/workspace",
            *test_command_argv(command),
        ],
        text=True,
        capture_output=True,
        timeout=600,
    )


class HermesWorkerManager:
    def __init__(
        self,
        root: Path,
        hermes: str,
        supervisorctl: str,
        gate: MutationGate | None = None,
    ):
        self.root = root
        self.hermes = hermes
        self.supervisorctl = supervisorctl
        self.prompts = PromptFactory()
        self.decomposition = SemanticDecompositionEngine(root)
        self.gate = gate or MutationGate(root, source="worker")
        self.accounting = AccountingLedger(root)

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
        role: str,
        decision: GateDecision | None,
        operation: OperationClass = OperationClass.MODEL_CALL,
        toolsets: str | None = None,
    ) -> str:
        repository = assignment.get("project") if operation != OperationClass.MODEL_CALL else None
        self.gate.require(
            decision,
            operation,
            assignment=assignment,
            repository=repository,
        )
        control = read_record(
            self.root / "control.json",
            "axis.external-development-supervisor.control",
        )
        prompt_bytes = len(prompt.encode("utf-8"))
        maximum_prompt_bytes = int(control.get("max_semantic_prompt_bytes", 200_000))
        if prompt_bytes > maximum_prompt_bytes:
            raise ValueError(
                f"worker prompt exceeds bounded size: {prompt_bytes}/{maximum_prompt_bytes} bytes"
            )
        lease = load_canonical_lease(self.root, assignment)
        attempt = self.accounting.start(
            role=role,
            model=model,
            provider="openai-api",
            run=str(assignment.get("created_by_run") or "unknown-run"),
            assignment=assignment["assignment_id"],
            limit=int(control.get("daily_model_call_limit", 0)),
        )
        stop_heartbeat = threading.Event()

        def heartbeat() -> None:
            while not stop_heartbeat.wait(300):
                subprocess.run(
                    [
                        sys.executable,
                        self.supervisorctl,
                        "heartbeat",
                        assignment["assignment_id"],
                        "--token",
                        lease["fencing_token"],
                    ],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        process = None
        try:
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
                stdin=subprocess.PIPE,
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
            if process.returncode != 0:
                raise RuntimeError(
                    f"worker failed ({process.returncode}): {output[-4000:]}"
                )
        except Exception as exc:
            self.accounting.finish(
                attempt,
                "failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join(timeout=5)
        self.accounting.finish(attempt, "succeeded")
        return output

    def semantic(
        self, assignment: dict, decision: GateDecision | None = None
    ) -> dict:
        self.gate.require(decision, OperationClass.MODEL_CALL, assignment=assignment)
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
        prompt = self.prompts.semantic_prompt(assignment)
        output = ""
        record = None
        for attempt in range(2):
            output = self.run_model(
                "gpt-5.4",
                prompt,
                900,
                assignment,
                "semantic",
                decision,
                toolsets="",
            )
            try:
                record = self.extract_json(output)
                break
            except ValueError:
                assignment.setdefault("invalid_model_outputs", []).append(
                    {"attempt": attempt + 1, "output_tail": output[-4000:]}
                )
                if attempt == 0:
                    prompt = (
                        "Your prior response violated the JSON-only contract. Return exactly one JSON object "
                        "matching the supplied schema, with no prose or markdown.\n\n"
                        + prompt
                    )
                    continue
                raise ValueError(
                    f"worker output contained no JSON object: {output[-1000:]}"
                )
        if record is None:
            raise ValueError("worker produced no semantic record")
        record["source_inventory_generation_id"] = assignment.get(
            "source_inventory_generation_id"
        )
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

    def technical_revalidation(
        self, assignment: dict, decision: GateDecision | None = None
    ) -> dict:
        self.gate.require(decision, OperationClass.MODEL_CALL, assignment=assignment)
        repo = Path("/home/cdenneen/src/workspace/personal/work") / assignment[
            "project"
        ].split("/")[-1]
        worktree = self.root / "worktrees" / assignment["assignment_id"]
        reconciliation = self.gate.decide(
            OperationClass.RECONCILIATION,
            assignment=assignment,
            repository=assignment["project"],
        )
        self.gate.require(
            reconciliation,
            OperationClass.RECONCILIATION,
            assignment=assignment,
            repository=assignment["project"],
        )
        subprocess.run(
            ["git", "clone", "--no-hardlinks", str(repo), str(worktree)],
            check=True,
            timeout=120,
        )
        main_sha = str((assignment.get("source_item") or {}).get("repository_head") or "")
        if not main_sha:
            raise RuntimeError("technical revalidation has no source main revision")
        subprocess.run(
            ["git", "checkout", "--detach", main_sha],
            cwd=worktree,
            check=True,
            timeout=60,
        )
        results = []
        try:
            for command in assignment.get("required_tests") or []:
                completed = run_isolated_test(worktree, command)
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
            return self.semantic(assignment, decision)
        finally:
            self.gate.require(
                reconciliation,
                OperationClass.RECONCILIATION,
                assignment=assignment,
                repository=assignment["project"],
            )
            shutil.rmtree(worktree, ignore_errors=True)

    def implementation(
        self,
        assignment: dict,
        repo: Path,
        repository_decision: GateDecision | None = None,
        model_decision: GateDecision | None = None,
    ) -> dict:
        self.gate.require(
            repository_decision,
            OperationClass.REPOSITORY,
            assignment=assignment,
            repository=assignment.get("project"),
        )
        branch = f"hermes/{assignment['assignment_id']}"
        worktree = self.root / "worktrees" / assignment["assignment_id"]
        remote_url = subprocess.check_output(
            ["git", "remote", "get-url", "origin"], cwd=repo, text=True, timeout=30
        ).strip()
        self._git_mutation(
            ["git", "clone", "--no-hardlinks", str(repo), str(worktree)],
            self.root,
            repository_decision,
            OperationClass.REPOSITORY,
            assignment,
            timeout=120,
        )
        self._git_mutation(
            ["git", "remote", "set-url", "origin", remote_url],
            worktree,
            repository_decision,
            OperationClass.REPOSITORY,
            assignment,
        )
        self._git_mutation(
            ["git", "switch", "-c", branch, "origin/main"],
            worktree,
            repository_decision,
            OperationClass.REPOSITORY,
            assignment,
        )
        allowed = set(assignment.get("allowed_paths") or [])
        source_files = {}
        for relative in sorted(allowed):
            path = resolve_allowed_source(worktree, relative)
            source_files[relative] = (
                path.read_text(encoding="utf-8") if path.is_file() else None
            )
        prompt = self.prompts.implementation_prompt(assignment, source_files)
        assignment["worktree"] = str(worktree)
        output = self.run_model(
            "gpt-5.3-codex",
            prompt,
            1800,
            assignment,
            "implementation",
            model_decision,
            OperationClass.MODEL_CALL,
            toolsets="",
        )
        handoff = self.extract_json(output)
        patch = str(handoff.get("patch") or "")
        if not patch.strip():
            raise RuntimeError("implementation planner returned an empty patch")
        patch_path = self.root / "recovery" / f"{assignment['assignment_id']}.planned.patch"
        patch_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.gate.require(
            repository_decision,
            OperationClass.REPOSITORY,
            assignment=assignment,
            repository=assignment.get("project"),
        )
        patch_path.write_text(patch, encoding="utf-8")
        patch_path.chmod(0o600)
        self._git_mutation(
            ["git", "apply", "--index", str(patch_path)],
            worktree,
            repository_decision,
            OperationClass.REPOSITORY,
            assignment,
        )
        changed = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only"],
            cwd=worktree,
            text=True,
            timeout=60,
        ).splitlines()
        disallowed = [path for path in changed if path not in allowed]
        if disallowed:
            raise RuntimeError(f"planner changed paths outside assignment: {disallowed}")
        planned_diff = subprocess.check_output(
            ["git", "diff", "--cached"], cwd=worktree, text=True, timeout=60
        )
        test_results = []
        for command in assignment.get("required_tests") or []:
            self.gate.require(
                repository_decision,
                OperationClass.REPOSITORY,
                assignment=assignment,
                repository=assignment.get("project"),
            )
            completed = run_isolated_test(worktree, command)
            test_results.append({"command": command, "returncode": completed.returncode})
            if completed.returncode != 0:
                raise RuntimeError(f"implementation test failed: {command}")
        if subprocess.check_output(
            ["git", "diff", "--cached"], cwd=worktree, text=True, timeout=60
        ) != planned_diff:
            raise RuntimeError("implementation tests changed the staged patch")
        unsafe_status = [
            line
            for line in subprocess.check_output(
                ["git", "status", "--porcelain"],
                cwd=worktree,
                text=True,
                timeout=60,
            ).splitlines()
            if line.startswith("??") or len(line) < 2 or line[1] != " "
        ]
        if unsafe_status:
            raise RuntimeError(
                f"implementation tests left unstaged/untracked changes: {unsafe_status}"
            )
        self._git_mutation(
            ["git", "config", "user.name", "AXIS Development Supervisor"],
            worktree,
            repository_decision,
            OperationClass.REPOSITORY,
            assignment,
        )
        self._git_mutation(
            ["git", "config", "user.email", "axis-supervisor@localhost"],
            worktree,
            repository_decision,
            OperationClass.REPOSITORY,
            assignment,
        )
        self._git_mutation(
            [
                "git",
                "commit",
                "--no-verify",
                "-m",
                str(assignment.get("title") or assignment["assignment_id"]),
            ],
            worktree,
            repository_decision,
            OperationClass.REPOSITORY,
            assignment,
            timeout=120,
        )
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=worktree, text=True, timeout=30
        ).strip()
        handoff["commit"] = head
        handoff["tests"] = test_results
        return {
            "branch": branch,
            "worktree": str(worktree),
            "custody": "isolated-clone",
            "changed_paths": changed,
            "commit": head,
            "handoff": handoff,
            "raw_output": output[-4000:],
        }

    def _git_mutation(
        self,
        command: list[str],
        cwd: Path,
        decision: GateDecision | None,
        operation: OperationClass,
        assignment: dict,
        *,
        check: bool = True,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess:
        repository = assignment.get("project")
        self.gate.require(
            decision,
            operation,
            assignment=assignment,
            repository=repository,
        )
        return subprocess.run(
            command,
            cwd=cwd,
            check=check,
            timeout=timeout,
        )
