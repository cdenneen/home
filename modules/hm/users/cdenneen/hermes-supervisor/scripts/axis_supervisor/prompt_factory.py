import json


class PromptFactory:
    def semantic_prompt(self, assignment: dict) -> str:
        source = assignment.get("source_item") or {}
        assignment_context = {
            key: value
            for key, value in assignment.items()
            if key not in {"source_item", "semantic_evidence", "worker"}
        }
        source_summary = {
            key: value for key, value in source.items() if key != "source_evidence"
        }
        evidence = assignment.get("semantic_evidence") or {}
        return f"""
You are a disposable Hermes semantic-analysis worker for the external AXIS Development Supervisor.
Analyze exactly one target and return JSON only. Do not modify repositories, GitLab, supervisor state, or credentials.

Target assignment:
{json.dumps(assignment_context, indent=2)}

Source summary:
{json.dumps(source_summary, indent=2)}

Canonical evidence packet:
{json.dumps(evidence, indent=2)}

Inspect canonical GitLab/repository evidence needed to evaluate bounded child work. Return this schema:
{{
  "schema": "axis.external-development-supervisor.semantic-record",
  "schema_version": "1.0.0",
  "target_ref": "{assignment.get('target_ref')}",
  "source_fingerprint": "{assignment.get('source_fingerprint')}",
  "evidence_fingerprint": "{assignment.get('evidence_fingerprint')}",
  "candidate_slices": [{{
    "slice_id": "stable-id",
    "title": "bounded title",
    "category": "research|audit|preparation|tests|fixtures|instrumentation|documentation|ci|convergence|benchmark|negative-test|compatibility|migration-rehearsal|evidence|implementation",
    "result": "Executable|Waiting|Blocked|Invalid",
    "rationale": "source-grounded rationale",
    "project": "group/project or null",
    "allowed_paths": [],
    "required_tests": [],
    "ranking_score": 0
  }}],
  "evidence_inspected": [{{"ref":"URL/path","claim":"what it proves"}}],
  "permitted_actions": [],
  "prohibited_actions": [],
  "direct_blocker": null,
  "transitive_blocker_chain": [],
  "authority_source": [],
  "authority_resolution": {{
    "state": "inherited|preparation-only|needs-product-owner|needs-governance|prohibited|unresolved",
    "source_refs": [],
    "controlling_parent": null,
    "parent_digest": null,
    "rationale": "",
    "permitted_effects": [],
    "prohibited_effects": []
  }},
  "next_state_changing_event": "",
  "verification_result": {{
    "standard": "Supervisor 1.1 audit standard",
    "tier": "{assignment.get('revalidation_tier') or 'B'}",
    "disposition": "verified-complete|active-technical-revalidation|corrective-implementation-required|human-authority-required",
    "checks": {{
      "current_main_and_merge_rechecked": true,
      "acceptance_evidence_rechecked": true,
      "required_tests_rechecked": true,
      "pipeline_rechecked": true,
      "governance_linkage_rechecked": true,
      "closure_rechecked": true,
      "integration_rechecked": true,
      "cleanup_rechecked": true,
      "fresh_cycle_recognition": true
    }},
    "evidence": [],
    "failed_checks": [],
    "failure_disposition": ""
  }},
  "decision_packet": null,
  "revalidated_at": "RFC3339"
}}

Candidate slices must be genuinely independent and governed. If none exists, return candidates considered as Waiting/Blocked with exact blocker chains. Never expose chain-of-thought.
For Tier A evidence-only revalidation, mark a check true only when the supplied canonical evidence proves it. `failed_checks` must list exactly every check that is false or null. Use `verified-complete` only when all nine checks are true, evidence is non-empty, failed_checks is empty, and failure_disposition is empty. Missing evidence is not failure of historical implementation: return `active-technical-revalidation` with exact failed checks, a non-empty failure_disposition, and an Executable audit/tests candidate containing bounded allowlisted required_tests for the Tier B action. Use `corrective-implementation-required` only when current evidence proves a requirement is no longer satisfied. Use `human-authority-required` only for reserved authority.
When authority state is `needs-product-owner`, `decision_packet` must contain current_record, current_digest, decision_requested, recommendation, consequences, downstream_effects, unresolved_assumptions, and exact response_syntax. Revalidate later approvals/superseding decisions before requesting one.
""".strip()

    def implementation_prompt(
        self, assignment: dict, source_files: dict[str, str | None]
    ) -> str:
        return f"""
You are a disposable no-tool patch planner using GPT-5.3-Codex.
Assignment: {json.dumps(assignment, indent=2)}
Allowlisted source files:
{json.dumps(source_files, indent=2)}

Return a unified diff that changes only allowed paths. Do not invoke tools, run commands, access repositories, use credentials, or perform any external effect. The supervisor applies the patch, runs the declared tests, commits, and publishes through its mutation gate.

End with JSON only:
{{"patch":"unified diff","wwwhh":{{"who":"","what":"","when":"","where":"","how":"","handoff":""}}}}
""".strip()
