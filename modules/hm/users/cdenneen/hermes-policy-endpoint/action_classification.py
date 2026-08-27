"""
Deterministic action-level continuity classification (Bootstrap v1).

Two independent per-request classification inputs, each producing a
continuity_class *ceiling*; the effective continuity_class for a request
is the most restrictive of every ceiling in play (gateway config ceiling,
tool-based ceiling, workload-source ceiling). This is the enforcement
side of continuity-class-audit.md's finding: gateway/profile config must
carry only a ceiling, never the final classification - real admission
classifies by action/workload first.

Tool classification reads the outbound ``tools`` array Hermes already
puts on every Chat Completions request (confirmed live:
agent/transports/chat_completions.py's ``api_kwargs["tools"] = tools``,
standard OpenAI ``{"type": "function", "function": {"name": ...}}``
shape - confirmed against agent/memory_manager.py, agent/relay_llm.py,
agent/bedrock_adapter.py). No Hermes modification needed for this input.

Bootstrap v1 posture is deliberately conservative: every GitLab mutation
tool maps to human-present even though some (a reversible, idempotent
create_branch already authorized by an open work package, for instance)
may later qualify for automatic. Per instruction this must never be
silently treated as permanent - every table entry carries an explicit
``revisable_with_evidence`` flag. The Adaptive phase may only relax a
*specific* entry by producing evidence (bounded, reversible, idempotent,
already authorized by the work package) and bumping POLICY_VERSION -
never by loosening the unknown-*effect* fallback, which must always deny
automatic continuity.

GOVERNING RULE (PO decision, 2026-08-27 - see
roadmap-amendment-unified-topology-workstreams-global-economics-developer-clients.md
"Conflict 1"): unknown purpose/workload does NOT imply dangerous effect;
unknown EFFECT does. These are deliberately different axes, not a single
"unknown -> restrictive" rule:

  - Missing or unrecognized workload/source metadata (``source_ceiling``
    below) grants no additional authority, but must not by itself
    prohibit otherwise independently proven read-only work. Its "unknown"
    ceiling is therefore the NEUTRAL value ("automatic") - a true identity
    element for `most_restrictive`, so it can never make an outcome more
    permissive than the tool/gateway ceilings, and never less permissive
    than a request already proven read_only by its actual tool
    classification.
  - An unrecognized TOOL name (``classify_tools``/``EFFECT_CLASS_CEILING``
    below) has an unverified EFFECT - we do not know whether it mutates -
    so it keeps the restrictive ceiling (manual-break-glass) regardless of
    how permissive the source is. This is the one case that legitimately
    fails closed.

This is also why governor.py's separate ``UNKNOWN_WORKLOAD_DEFAULT``
(continuity_class="automatic-read-only") does not conflict with this
module's unknown-tool ceiling (manual-break-glass): that default answers
"what does an uncharacterized *workload* get," this module answers "what
does a request whose *effect* cannot be verified get" - the same
uniform-vs-effect-specific split as above. Do not "normalize" the two
into one value - see TestUnknownPurposeVsUnknownEffect in
test_endpoint.py for the regression coverage that guards this.
"""

from dataclasses import dataclass
from enum import Enum

POLICY_VERSION = "bootstrap_v1"

# Most permissive -> least permissive. "Effective" continuity_class for a
# request is the *last* (most restrictive) of every ceiling that applies.
CONTINUITY_ORDER = [
    "automatic",
    "automatic-read-only",
    "human-present",
    "manual-break-glass",
    "unavailable",
]


class EffectClass(str, Enum):
    READ_ONLY = "read_only"
    BOUNDED_MUTATION = "bounded_mutation"
    HIGH_IMPACT = "high_impact"
    UNKNOWN = "unknown"  # never assigned in the table itself - only the deny-automatic fallback


_EFFECT_CLASS_ORDER = [
    EffectClass.READ_ONLY,
    EffectClass.BOUNDED_MUTATION,
    EffectClass.HIGH_IMPACT,
    EffectClass.UNKNOWN,
]

EFFECT_CLASS_CEILING = {
    EffectClass.READ_ONLY: "automatic-read-only",
    EffectClass.BOUNDED_MUTATION: "human-present",
    EffectClass.HIGH_IMPACT: "manual-break-glass",
    # Unknown tool name: deny automatic continuity - same ceiling as a
    # known destructive tool, never a permissive default.
    EffectClass.UNKNOWN: "manual-break-glass",
}


@dataclass(frozen=True)
class ToolClassification:
    tool: str
    effect_class: EffectClass
    rationale: str
    revisable_with_evidence: bool = False


def _ro(tool: str, rationale: str) -> ToolClassification:
    return ToolClassification(tool, EffectClass.READ_ONLY, rationale)


def _bounded(tool: str, rationale: str) -> ToolClassification:
    return ToolClassification(tool, EffectClass.BOUNDED_MUTATION, rationale, revisable_with_evidence=True)


def _high(tool: str, rationale: str) -> ToolClassification:
    return ToolClassification(tool, EffectClass.HIGH_IMPACT, rationale, revisable_with_evidence=False)


# GitLab MCP tool catalog (@zereight/mcp-gitlab), wired into Nyx's EKS and
# GitLab Hermes profiles (mcp_servers.gitlab, confirmed via `hermes mcp test
# gitlab` - 23 tools). This is the durable policy-evidence table requested:
# every tool this program has ever seen offered gets one row here, with a
# rationale, before it can be classified more permissively than the
# unknown-tool fallback.
GITLAB_TOOL_TABLE: dict[str, ToolClassification] = {
    c.tool: c
    for c in [
        _ro("get_project", "read-only lookup"),
        _ro("list_projects", "read-only lookup"),
        _ro("search_repositories", "read-only lookup"),
        _ro("get_file_contents", "read-only lookup"),
        _ro("list_issues", "read-only lookup"),
        _ro("get_issue", "read-only lookup"),
        _ro("list_merge_requests", "read-only lookup"),
        _ro("get_merge_request", "read-only lookup"),
        _ro("get_branch_diffs", "read-only lookup"),
        _ro("list_branches", "read-only lookup"),
        _ro("get_branch", "read-only lookup"),
        _bounded("create_issue", "project-scoped mutation, reversible (close/reopen/edit)"),
        _bounded("update_issue", "project-scoped mutation, reversible"),
        _bounded("create_branch", "mutation, reversible (delete branch)"),
        _bounded("create_or_update_file", "mutation, reversible via git history/revert"),
        _bounded("push_files", "mutation, reversible via git history/revert"),
        _bounded("create_merge_request", "mutation, reversible (close MR)"),
        _bounded("create_note", "low-consequence mutation, reversible (edit/delete comment)"),
        _bounded("mr_discussions", "low-consequence mutation, reversible"),
        _high("merge_merge_request", "mutates protected default branch, not cleanly reversible, triggers downstream CI/deploy"),
        _high("delete_branch", "destructive, not reversible without remote history"),
    ]
}


def classify_tools(tool_names: list[str]) -> tuple[EffectClass, list[ToolClassification]]:
    """Most restrictive effect_class among every tool offered in one request.

    An unknown tool name is never assumed safe - it is classified UNKNOWN
    (ceiling: manual-break-glass, same as a known destructive tool), not
    defaulted to read_only or silently ignored.
    """
    if not tool_names:
        return EffectClass.READ_ONLY, []
    classifications = []
    for name in tool_names:
        known = GITLAB_TOOL_TABLE.get(name)
        if known is None:
            classifications.append(
                ToolClassification(
                    name,
                    EffectClass.UNKNOWN,
                    f"'{name}' not present in {POLICY_VERSION} classification table - deny automatic continuity",
                )
            )
        else:
            classifications.append(known)
    worst = max(classifications, key=lambda c: _EFFECT_CLASS_ORDER.index(c.effect_class))
    return worst.effect_class, classifications


# Workload-source ceilings (piece 2 input), per continuity-class-audit.md's
# per-consumer recommendations. Not yet a full per-cron-job inventory - a
# known gap, tracked there, not resolved by this table.
#
# No "subagent" entry (PO decision, roadmap amendment #38, 2026-08-27):
# subagent is execution metadata, not a continuity determinant by itself -
# a delegated request is classified by its actual tools exactly like any
# other request (read-only delegated research -> automatic-read-only via
# classify_tools([]) / known read_only tools; mutation-capable delegated
# work -> the mutation's own ceiling; unbounded/unknown effect -> fails
# closed via EFFECT_CLASS_CEILING[UNKNOWN]). A blanket subagent->unavailable
# rule halted safe autonomous delegation solely because its execution
# surface was autonomous, which is exactly the failure mode
# execution-contract.md 10 exists to prevent.
SOURCE_CONTINUITY_CEILING: dict[str, str] = {
    "cron": "automatic-read-only",
    "compression": "automatic-read-only",
    "title_generation": "automatic-read-only",
    "vision": "automatic-read-only",
    "approval": "automatic-read-only",
    "cli": "human-present",
    "slack": "human-present",
    "kanban": "human-present",
}
# Missing/unrecognized source (PO decision, roadmap amendment #37,
# 2026-08-27): NEUTRAL, not restrictive. "automatic" is the identity
# element for most_restrictive() - grants no additional authority (never
# beats a real gateway/tool ceiling) but never itself prohibits a request
# whose effect is independently proven read_only. Unverified TOOL effect
# still fails closed via EFFECT_CLASS_CEILING[EffectClass.UNKNOWN] - that
# is the one axis where "unknown" legitimately means "deny."
UNKNOWN_SOURCE_CEILING = "automatic"


def source_ceiling(source) -> str:
    if not source:
        return UNKNOWN_SOURCE_CEILING
    return SOURCE_CONTINUITY_CEILING.get(source, UNKNOWN_SOURCE_CEILING)


def most_restrictive(*classes: str) -> str:
    return max(classes, key=CONTINUITY_ORDER.index)


def effective_continuity_class(
    gateway_ceiling: str, tool_names: list[str], workload_source
) -> tuple[str, dict]:
    """Combine the gateway's static ceiling with this request's tool- and
    source-derived ceilings. Returns (effective_class, evidence) - evidence
    is durable policy proof, not just a debug log line.

    Provenance (roadmap amendment #39A - caller assertions may narrow
    authority but never widen it):
      - gateway_ceiling: ATTESTED - the caller of this function supplies it
        from the instance's static Nix-rendered config, never from request
        body content; endpoint.py never reads a trust_domain/agent/
        workstream/continuity_class field out of the inbound HTTP body.
      - tool_ceiling: DERIVED - computed from the actual `tools` array
        Hermes sends the model on the wire, not a self-reported
        "effect_class" claim; there is no caller-supplied effect_class
        input for this function to trust or distrust.
      - source_ceiling: ASSERTED - `workload_source` (`x_hermes_source`)
        arrives in the request body, populated by the workload-metadata
        sitecustomize patch (piece 2), not by an authenticated/attested
        channel. `most_restrictive()`'s max()-over-all-ceilings structure
        makes this provably FLOOR-SAFE: a false/omitted source assertion
        can never push `effective` below what `most_restrictive(
        gateway_ceiling, tool_ceiling)` alone would already produce (see
        TestMetadataProvenanceAndAuthorityBoundary.
        test_source_assertion_can_never_widen_below_the_gateway_tool_floor
        in test_endpoint.py for the proof-by-fuzzing). What it can do is
        fail to ADD a truthful additional restriction the real source
        would have justified (e.g. mis-reporting "cron" instead of the
        true "slack") - a real, accepted Bootstrap-tier gap, not a
        privilege escalation past the gateway/tool floor. Closing it fully
        requires binding x_hermes_source to an attested channel (HMAC/
        process-identity), tracked as future work (#39E/#39F), not done
        in this slice.
    """
    effect_class, tool_classifications = classify_tools(tool_names)
    tool_ceiling = EFFECT_CLASS_CEILING[effect_class]
    src_ceiling = source_ceiling(workload_source)
    effective = most_restrictive(gateway_ceiling, tool_ceiling, src_ceiling)
    evidence = {
        "policy_version": POLICY_VERSION,
        "gateway_ceiling": gateway_ceiling,
        "gateway_ceiling_provenance": "attested",
        "tool_effect_class": effect_class.value,
        "tool_ceiling": tool_ceiling,
        "tool_ceiling_provenance": "derived",
        "tools_seen": [c.tool for c in tool_classifications],
        "unknown_tools": [c.tool for c in tool_classifications if c.effect_class == EffectClass.UNKNOWN],
        "workload_source": workload_source,
        "source_ceiling": src_ceiling,
        "source_ceiling_provenance": "asserted",
        "effective_continuity_class": effective,
    }
    return effective, evidence


# Eligibility under each continuity mode. continuity_auto has no human
# present by construction, so human-present and manual-break-glass are
# both denied there regardless of priority - continuity_class is
# orthogonal to priority (execution-contract.md 10.4), not a restatement
# of it.
CONTINUITY_AUTO_ALLOWED = {"automatic", "automatic-read-only"}
BREAK_GLASS_ALLOWED = {"automatic", "automatic-read-only", "human-present", "manual-break-glass"}


def continuity_mode_permits(mode: str, continuity_class: str) -> bool:
    if mode == "continuity_auto":
        return continuity_class in CONTINUITY_AUTO_ALLOWED
    if mode == "break_glass":
        return continuity_class in BREAK_GLASS_ALLOWED
    return True  # normal mode: economic-state admission gates it, not continuity_class
