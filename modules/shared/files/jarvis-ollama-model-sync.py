from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover
    yaml = None


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def read_yaml(path: Path) -> dict[str, Any]:
    if yaml is None or not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_state(path: Path, payload: dict[str, Any]) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync repo-defined Ollama models for Jarvis tiers")
    parser.add_argument("--ollama-endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--models-file", required=True)
    parser.add_argument("--state-file", default="/var/lib/jarvis/data/ollama_model_sync_state.json")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    models_path = Path(args.models_file)
    state_path = Path(args.state_file)
    cfg = read_yaml(models_path)
    tiers = cfg.get("tiers") if isinstance(cfg.get("tiers"), dict) else {}
    auto_pull = bool(cfg.get("auto_pull", True))

    state: dict[str, Any] = {
        "timestamp": now_iso(),
        "ok": True,
        "models_file": str(models_path),
        "ollama_endpoint": args.ollama_endpoint,
        "results": [],
    }

    if not tiers:
        state["ok"] = False
        state["error"] = "no tiers defined in models file"
        write_state(state_path, state)
        raise SystemExit(1)

    try:
        with httpx.Client(timeout=args.timeout) as client:
            tags_resp = client.get(f"{args.ollama_endpoint.rstrip('/')}/api/tags")
            tags_resp.raise_for_status()
            tags_data = tags_resp.json() if tags_resp.headers.get("content-type", "").startswith("application/json") else {}
            present = set()
            if isinstance(tags_data, dict) and isinstance(tags_data.get("models"), list):
                for row in tags_data.get("models", []):
                    if isinstance(row, dict):
                        name = str(row.get("name", "")).strip()
                        if name:
                            present.add(name)

            for tier, raw in tiers.items():
                item = raw if isinstance(raw, dict) else {}
                alias = str(item.get("name", "")).strip()
                source = str(item.get("source", "")).strip() or alias
                if not alias:
                    state["results"].append({"tier": tier, "ok": False, "detail": "missing name"})
                    state["ok"] = False
                    continue

                result = {"tier": tier, "alias": alias, "source": source, "ok": True, "actions": []}
                if alias in present:
                    result["actions"].append("already-present")
                    state["results"].append(result)
                    continue

                if not auto_pull:
                    result["ok"] = False
                    result["detail"] = "missing and auto_pull=false"
                    state["ok"] = False
                    state["results"].append(result)
                    continue

                pull_resp = client.post(f"{args.ollama_endpoint.rstrip('/')}/api/pull", json={"name": source, "stream": False})
                pull_resp.raise_for_status()
                result["actions"].append(f"pulled:{source}")

                if alias != source:
                    cp_resp = client.post(f"{args.ollama_endpoint.rstrip('/')}/api/copy", json={"source": source, "destination": alias})
                    cp_resp.raise_for_status()
                    result["actions"].append(f"aliased:{source}->{alias}")

                state["results"].append(result)
    except Exception as exc:
        state["ok"] = False
        state["error"] = str(exc)

    write_state(state_path, state)
    if not state.get("ok", False):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
