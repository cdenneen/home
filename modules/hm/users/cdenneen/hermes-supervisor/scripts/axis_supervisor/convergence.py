import json
import subprocess
import time
from pathlib import Path


class RepositoryConvergenceEngine:
    def capture(self, repo: Path, branch: str, output: Path) -> dict:
        def run(*args: str) -> str:
            return subprocess.check_output(["git", *args], cwd=repo, text=True, timeout=60)

        evidence = {
            "captured_at_epoch": int(time.time()),
            "repository": str(repo),
            "branch": branch,
            "head": run("rev-parse", "HEAD").strip(),
            "upstream": run("rev-parse", "--abbrev-ref", "@{upstream}").strip()
            if subprocess.run(["git", "rev-parse", "@{upstream}"], cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
            else None,
            "status": run("status", "--porcelain=v2"),
            "diff_stat": run("diff", "--stat"),
            "untracked": run("ls-files", "--others", "--exclude-standard").splitlines(),
            "reflog": run("reflog", "-20", "--date=iso"),
        }
        output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        output.chmod(0o600)
        return evidence
