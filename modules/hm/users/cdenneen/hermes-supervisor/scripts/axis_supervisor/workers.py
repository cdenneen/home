import json
import os
import re
import signal
import subprocess
import threading
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
                self.hermes,
                "-z",
                prompt,
                "--provider",
                "openai-api",
                "-m",
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
            output, _ = process.communicate(timeout=timeout)
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
            notes = glab_json(f"projects/{encoded}/issues/{iid}/notes?per_page=100")
            events = glab_json(
                f"projects/{encoded}/issues/{iid}/resource_state_events?per_page=100"
            )
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
                "issue": issue,
                "notes": notes,
                "resource_state_events": events,
                "origin_main": subprocess.check_output(
                    ["git", "rev-parse", "origin/main"], cwd=repo, text=True
                ).strip(),
                "repository_references": grep.stdout.splitlines()[:100],
                "commit_references": history.stdout.splitlines(),
                "parents": [],
            }
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
        path = self.decomposition.save(record)
        return {"record": record, "path": str(path), "raw_output": output[-4000:]}

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
