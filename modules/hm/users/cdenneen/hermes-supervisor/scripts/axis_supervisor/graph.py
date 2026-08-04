import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .authority import AuthorityResolver
from .decomposition import SemanticDecompositionEngine


class ExecutionGraphBuilder:
    def __init__(self, root: Path):
        self.root = root
        self.decomposition = SemanticDecompositionEngine(root)
        self.authority = AuthorityResolver()

    def build(self, inventory: dict) -> dict:
        control = json.loads((self.root / "control.json").read_text(encoding="utf-8"))
        semantic_priority = {
            ref: 500 - index
            for index, ref in enumerate(control.get("semantic_priority_refs") or [])
        }
        nodes = []
        queue = []
        semantic_pending = 0
        semantic_unresolved = 0
        items_by_ref = {
            item.get("ref"): item for item in inventory.get("work_items") or []
        }
        for item in inventory.get("work_items") or []:
            source_fingerprint = self.decomposition.source_fingerprint(item)
            semantic = self.decomposition.load(item["ref"], source_fingerprint)
            controlling_parent = (
                (semantic or {}).get("authority_resolution") or {}
            ).get("controlling_parent")
            authority = self.authority.resolve(
                item, semantic, items_by_ref.get(controlling_parent)
            )
            node = {
                "ref": item["ref"],
                "kind": item.get("kind"),
                "classification": item.get("classification"),
                "authority": authority,
                "dependencies": item.get("dependencies") or [],
                "semantic_record": semantic,
                "source_fingerprint": source_fingerprint,
            }
            nodes.append(node)
            if semantic is not None and authority["state"] in {
                "unresolved",
                "needs-product-owner",
                "needs-governance",
            }:
                semantic_unresolved += 1
            if item.get("classification") in {
                "Waiting",
                "Blocked",
                "Revalidation",
                "Integrated",
                "Completed",
            } and semantic is None:
                pending = self.decomposition.pending_item(item)
                pending["source_fingerprint"] = source_fingerprint
                pending["ranking_score"] = semantic_priority.get(item["ref"], pending["ranking_score"])
                queue.append(pending)
                semantic_pending += 1
                continue
            if semantic is not None:
                executable_candidates = 0
                for candidate in semantic.get("candidate_slices") or []:
                    if candidate.get("result") != "Executable":
                        continue
                    category = candidate.get("category")
                    mutating = category in {"implementation", "ci", "convergence"}
                    if not control.get("allow_repository_mutation"):
                        continue
                    if mutating and authority["state"] not in {"direct", "inherited"}:
                        continue
                    if not mutating and authority["state"] not in {
                        "direct",
                        "inherited",
                        "preparation-only",
                    }:
                        continue
                    executable_candidates += 1
                    queue.append(
                        {
                            "ref": f"slice:{item['ref']}:{candidate['slice_id']}",
                            "kind": "implementation",
                            "target_ref": item["ref"],
                            "project": candidate.get("project") or item.get("project"),
                            "title": candidate.get("title"),
                            "classification": "Executable",
                            "ranking_score": int(candidate.get("ranking_score") or 200),
                            "authority": authority,
                            "candidate": candidate,
                            "source_fingerprint": source_fingerprint,
                        }
                    )
                if (
                    executable_candidates == 0
                    and item.get("classification") == "Executable"
                    and control.get("allow_repository_mutation")
                    and authority["state"] in {"direct", "inherited"}
                ):
                    queue.append(item)
            elif (
                item.get("classification") == "Executable"
                and control.get("allow_repository_mutation")
                and authority["state"] in {"direct", "inherited"}
            ):
                queue.append(item)

        queue.sort(key=lambda item: (-int(item.get("ranking_score") or 0), item["ref"]))
        classifier_empty = not inventory.get("executable_queue")
        unresolved_convergence = any(
            node["kind"] == "repository-convergence"
            and node["classification"] in {"Waiting", "Blocked", "Running"}
            for node in nodes
        )
        source_idle = inventory.get("idle_proof") or {}
        governed_zero = bool(
            not queue
            and semantic_pending == 0
            and semantic_unresolved == 0
            and classifier_empty
            and not unresolved_convergence
            and source_idle.get("all_configured_repositories_inspected")
            and source_idle.get("unknown_count") == 0
            and source_idle.get("dependency_query_failures") == 0
            and source_idle.get("running_count") == 0
            and source_idle.get("active_assignment_count") == 0
            and source_idle.get("active_lease_count") == 0
        )
        graph = {
            "schema": "axis.external-development-supervisor.execution-graph",
            "schema_version": "1.0.0",
            "generation_id": str(uuid.uuid4()),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "inventory_generation_id": inventory.get("generation_id"),
            "nodes": nodes,
            "edges": inventory.get("execution_graph", {}).get("edges") or [],
            "executable_queue": queue,
            "queue_depth": len(queue),
            "semantic_decomposition_pending": semantic_pending,
            "semantic_authority_unresolved": semantic_unresolved,
            "classifier_queue_empty": classifier_empty,
            "governed_queue_zero_proven": governed_zero,
        }
        path = self.root / "execution-graph.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(path)
        return graph
