import json


class PromptFactory:
    def semantic_prompt(self, assignment: dict) -> str:
        source = assignment.get("source_item") or {}
        return f"""
You are a disposable Hermes semantic-analysis worker for the external AXIS Development Supervisor.
Analyze exactly one target and return JSON only. Do not modify repositories, GitLab, supervisor state, or credentials.

Target assignment:
{json.dumps(assignment, indent=2)}

Source item:
{json.dumps(source, indent=2)}

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
  "decision_packet": null,
  "revalidated_at": "RFC3339"
}}

Candidate slices must be genuinely independent and governed. If none exists, return candidates considered as Waiting/Blocked with exact blocker chains. Never expose chain-of-thought.
When authority state is `needs-product-owner`, `decision_packet` must contain current_record, current_digest, decision_requested, recommendation, consequences, downstream_effects, unresolved_assumptions, and exact response_syntax. Revalidate later approvals/superseding decisions before requesting one.
""".strip()

    def implementation_prompt(self, assignment: dict, worktree: str) -> str:
        return f"""
You are a disposable Hermes-native coding worker using GPT-5.3-Codex.
Assignment: {json.dumps(assignment, indent=2)}
Worktree: {worktree}

Implement only the bounded candidate and allowed paths. Run required tests and commit coherent changes on the assigned local branch. Do not push, create an MR, merge, or modify supervisor policy. The supervisor validates paths and publishes the branch/MR after your process exits.

End with JSON only:
{{"commit":"sha","tests":["command: result"],"wwwhh":{{"who":"","what":"","when":"","where":"","how":"","handoff":""}}}}
""".strip()

    def integration_prompt(self, assignment: dict, inspection: dict) -> str:
        return f"""
You are a fresh Hermes integration worker using GPT-5.4. You have no prior conversation context.
Assignment:
{json.dumps(assignment, indent=2)}
Current GitLab inspection:
{json.dumps(inspection, indent=2)}

Reconstruct from GitLab and the worktree. If CI/review is non-terminal, return Waiting without polling. If a bounded branch defect exists, repair only allowed paths, test, commit, push, and leave integration pending. If all configured gates pass, merge through GitLab and reconcile the work-item evidence. Do not remove the branch/worktree; the supervisor validates merged main and performs cleanup. Return JSON only:
{{"result":"integrated|waiting|repaired|blocked","merge_commit":"sha-or-null","main_sha":"sha-or-null","tests":["command: result"],"cleanup":{{"branch":true,"worktree":true}},"evidence":["url"],"next":""}}
""".strip()
