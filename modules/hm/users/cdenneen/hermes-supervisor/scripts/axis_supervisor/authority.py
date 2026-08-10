from .models import validate_semantic_record
from .canonical_work_item import projection_for


class AuthorityResolver:
    """Resolve direct, inherited, preparation-only, or prohibited authority."""

    def resolve(
        self, item: dict, semantic_record: dict | None, parent_item: dict | None = None
    ) -> dict:
        projection = projection_for(item)
        direct = projection.get("authority_facts") or item.get("authority_facts") or item.get("authority") or {}
        if item.get("canonical_work_item") and not projection.get("collection_complete_for_authority"):
            return {"state": "unresolved", "source": direct, "reason": "authority note collection is incomplete"}
        if direct.get("repository_convergence_authorized"):
            return {
                "state": "direct",
                "source": direct,
                "reason": "supervisor-owned integrated repository state",
            }
        if direct.get("decision_stop"):
            return {"state": "prohibited", "source": direct, "reason": "stop decision"}
        if direct.get("approval_mismatch"):
            return {"state": "needs-product-owner", "source": direct, "reason": "approval digest mismatch"}
        if direct.get("approval_matches_record"):
            return {"state": "direct", "source": direct, "reason": "exact approved PlanningRecord"}
        if semantic_record is None:
            return {"state": "unresolved", "source": direct, "reason": "semantic authority record missing"}
        record = validate_semantic_record(semantic_record)
        resolution = record.get("authority_resolution") or {}
        state = resolution.get("state")
        if state not in {
            "inherited",
            "preparation-only",
            "needs-product-owner",
            "needs-governance",
            "prohibited",
            "unresolved",
        }:
            return {"state": "unresolved", "source": resolution, "reason": "invalid semantic authority state"}
        if state == "inherited":
            parent_authority = (
                (parent_item or {}).get("authority_facts")
                or (parent_item or {}).get("authority")
                or {}
            )
            controlling_parent = resolution.get("controlling_parent")
            source_refs = resolution.get("source_refs") or []
            parent_digest = resolution.get("parent_digest")
            relationship_evidence = (
                controlling_parent
                in (
                    item.get("blocking_dependency_refs")
                    or item.get("dependencies")
                    or []
                )
                or controlling_parent
                in ((item.get("source_evidence") or {}).get("parent_refs") or [])
            )
            if (
                parent_item is None
                or parent_item.get("ref") != controlling_parent
                or not parent_authority.get("approval_matches_record")
                or controlling_parent not in source_refs
                or parent_digest != parent_authority.get("record_digest")
                or not relationship_evidence
            ):
                return {
                    "state": "unresolved",
                    "source": resolution.get("source_refs") or [],
                    "reason": "inherited authority is not backed by an exact approved parent",
                }
        return {
            "state": state,
            "source": resolution.get("source_refs") or [],
            "reason": resolution.get("rationale") or "semantic authority resolution",
            "parent": resolution.get("controlling_parent"),
            "permitted_effects": resolution.get("permitted_effects") or [],
            "prohibited_effects": resolution.get("prohibited_effects") or [],
        }
