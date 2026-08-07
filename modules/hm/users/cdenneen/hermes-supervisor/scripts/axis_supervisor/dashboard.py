import hashlib
import json
import re
from pathlib import Path

from .decisions import DECISION_ID, DecisionStore
from .schema_registry import read_record

DASHBOARD_PROOF_SECTIONS = (
    "Primary KPI",
    "Action Effectiveness",
    "Milestone Graduation",
    "Milestone Debt, Risk, Confidence & Forecast",
    "Work In Progress",
    "Recent Activity",
    "Current Constraint",
    "Engineering Progress",
    "Deployment Rings",
    "Runtime Validation",
    "Capability Graduation",
    "Ghost Runtime",
    "Ghost Web",
    "Nyx axis-node",
    "macbookpro axis-desktop",
    "mbair axis-desktop",
    "Human Action Required",
)

_INTERNAL_TERMS = (
    (r"\bworktrees?\b", "workspace"),
    (r"\bleases?\b", "reservation"),
    (r"\bgrants?\b", "authorization"),
    (r"\bmodel(?:-call)?\b", "execution"),
    (r"\baccounting\b", "metrics"),
    (r"\blifecycle\b", "delivery"),
    (r"\btimestamps?\b", "time"),
    (r"\bnext\b", "following"),
    (r"\bcompleted\b", "verified"),
)


def public_text(value: object) -> str:
    text = str(value or "")
    for pattern, replacement in _INTERNAL_TERMS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text.strip()


def progress_bar(value: int, total: int, width: int = 12) -> str:
    bounded_total = max(0, int(total))
    bounded_value = min(max(0, int(value)), bounded_total) if bounded_total else 0
    filled = round(width * bounded_value / bounded_total) if bounded_total else 0
    return "█" * filled + "░" * (width - filled)


def _load_capabilities(root: Path) -> dict:
    path = root / "capability-convergence.json"
    if not path.exists():
        return {}
    return read_record(
        path, "axis.external-development-supervisor.capability-convergence"
    )


def _load_graduation(root: Path) -> dict:
    path = root / "capability-graduation.json"
    if not path.exists():
        return {}
    return read_record(
        path, "axis.external-development-supervisor.capability-graduation"
    )


def _load_mission(root: Path) -> dict:
    path = root / "active-mission.json"
    if not path.exists():
        return {}
    return read_record(path, "axis.external-development-supervisor.active-mission")


def _runtime_status(record: dict | None, *, offline: bool = False) -> tuple[str, str]:
    if offline:
        return "⚪", "Offline"
    if not record:
        return "⚪", "Not observed"
    if (
        record.get("status") == "converged"
        and record.get("verification_status") == "verified"
    ):
        return "🟢", "Verified"
    if record.get("status") in {
        "deployment-required",
        "verification-required",
        "blocked-by-prior-ring",
    }:
        return "🟡", "Deployment pending"
    if record.get("status") == "unknown":
        return "🔴", "Validation unavailable"
    return "🟡", public_text(record.get("status") or "Validation pending").title()


def _recent_lines(events: list[dict]) -> tuple[list[str], int]:
    routine_no_ops = {
        event.get("assignment_id")
        for event in events
        if (event.get("details") or {}).get("assignment_type") == "no-op-verification"
    }
    labels = {
        "implementation_completed": "Engineering change prepared",
        "mr_created": "Engineering review opened",
        "mr_merged": "Engineering change integrated",
        "post_main_verified": "Mainline evidence verified",
        "capability_deployment_verified": "Runtime capability verified",
        "assignment_retry": "Corrective recovery started",
    }
    visible = []
    for event in reversed(events):
        label = labels.get(str(event.get("event_type") or ""))
        if not label:
            continue
        ref = public_text(event.get("work_item"))
        visible.append(f"• {label}" + (f" — `{ref}`" if ref else ""))
        if len(visible) == 3:
            break
    if not visible:
        visible.append("• No material change recorded in the current activity window")
    return visible, len(routine_no_ops - {None})


def _capability_gate_counts(capabilities: dict) -> tuple[int, int]:
    runtimes = {
        str(value.get("runtime")): value for value in capabilities.get("runtimes") or []
    }
    passed = 0
    total = 0
    for capability in capabilities.get("capabilities") or []:
        name = str(capability.get("capability") or "")
        for runtime_name in capability.get("projected_runtimes") or []:
            total += 1
            runtime = runtimes.get(str(runtime_name)) or {}
            if (
                runtime_name != "mbair"
                and runtime.get("status") == "converged"
                and runtime.get("verification_status") == "verified"
                and name not in (runtime.get("capabilities_behind") or [])
            ):
                passed += 1
    return passed, total


def render_executive_dashboard(
    root: Path,
    inventory: dict,
    graph: dict,
    semantics: dict,
    events: list[dict],
) -> tuple[str, list[dict], str]:
    total = int(semantics.get("total_governed_items") or 0)
    verified = int(
        ((semantics.get("composition") or {}).get("verified_complete") or {}).get(
            "count"
        )
        or 0
    )
    roadmap_percent = round(verified * 100 / total) if total else 0
    milestones = semantics.get("complete_roadmap") or []
    verified_milestones = sum(
        str(value.get("status") or "") in {"verified", "completed"}
        for value in milestones
    )
    scheduler = semantics.get("scheduler_state") or {}
    wip_counts = scheduler.get("wip_counts") or {}
    wip_limits = scheduler.get("wip_limits") or {}
    wip_total = sum(int(value or 0) for value in wip_counts.values())
    wip_limit = sum(int(value or 0) for value in wip_limits.values())
    constraint = scheduler.get("current_constraint") or {}
    recent, routine_no_ops = _recent_lines(events)
    capabilities = _load_capabilities(root)
    graduation = _load_graduation(root)
    mission = _load_mission(root)
    effectiveness = mission.get("effectiveness_metrics") or {}
    runtime_records = {
        str(value.get("runtime")): value for value in capabilities.get("runtimes") or []
    }
    deployed = sum(
        runtime_name != "mbair" and value.get("status") == "converged"
        for runtime_name, value in runtime_records.items()
    )
    validated = sum(
        runtime_name != "mbair" and value.get("verification_status") == "verified"
        for runtime_name, value in runtime_records.items()
    )
    runtime_total = max(4, len(runtime_records))
    gate_passed, gate_total = _capability_gate_counts(capabilities)
    primary_kpi = graduation.get("primary_kpi") or {
        "count": gate_passed,
        "denominator": gate_total,
        "percent": round(gate_passed * 100 / gate_total, 1) if gate_total else 0,
    }
    graduated_capabilities = int(primary_kpi.get("count") or 0)
    capability_total = int(primary_kpi.get("denominator") or 0)
    capability_percent = float(primary_kpi.get("percent") or 0)
    program_risk = graduation.get("program_risk") or {
        "score": 0,
        "level": "unknown",
    }
    operator_confidence = float(graduation.get("operator_confidence") or 0)
    milestone_graduation = graduation.get("milestones") or []
    milestone_debts = sum(
        len(value.get("debts") or []) for value in milestone_graduation
    )
    forecast_days = next(
        (
            (value.get("forecast") or {}).get("days")
            for value in milestone_graduation
            if not value.get("graduated")
            and (value.get("forecast") or {}).get("days") is not None
        ),
        None,
    )
    ghost = runtime_records.get("ghost")
    nyx = runtime_records.get("nyx")
    macbookpro = runtime_records.get("macbookpro")
    ghost_icon, ghost_status = _runtime_status(ghost)
    nyx_icon, nyx_status = _runtime_status(nyx)
    mac_icon, mac_status = _runtime_status(macbookpro)
    mbair_icon, mbair_status = _runtime_status(
        runtime_records.get("mbair"), offline=True
    )
    ghost_web_behind = "Web Presentation" in (
        (ghost or {}).get("capabilities_behind") or []
    )
    ghost_web_verified = bool(
        ghost
        and not ghost_web_behind
        and ghost.get("verification_status") == "verified"
    )
    ghost_web_icon = "🟢" if ghost_web_verified else "🟡" if ghost else "⚪"
    ghost_web_status = (
        "Verified"
        if ghost_web_verified
        else "Validation pending"
        if ghost
        else "Not observed"
    )
    pending_decisions = []
    decision_store = DecisionStore(root)
    for node in graph.get("nodes") or []:
        packet = (node.get("semantic_record") or {}).get("decision_packet")
        if not isinstance(packet, dict):
            continue
        decision_id = str(packet.get("decision_id") or node.get("ref") or "")
        if decision_id == DECISION_ID and decision_store.load(DECISION_ID) is not None:
            continue
        pending_decisions.append(decision_id)
    need_human = bool(pending_decisions) or (
        (semantics.get("supervisor_work") or {}).get("need_product_owner_now") == "Yes"
    )

    milestone_lines = []
    for milestone in milestones:
        progress = milestone.get("progress") or {}
        value = int(progress.get("count") or 0)
        denominator = int(progress.get("denominator") or 0)
        milestone_lines.append(
            f"• *{public_text(milestone.get('key'))}* `{progress_bar(value, denominator)}` "
            f"{value}/{denominator} — {public_text(milestone.get('status') or 'planned')}"
        )
    if not milestone_lines:
        milestone_lines.append("• No milestone proof is available")

    def host_capability(value: dict | None) -> int:
        return len((value or {}).get("capabilities_behind") or [])

    sections = [
        (
            "Primary KPI",
            f"Graduated capabilities `{progress_bar(graduated_capabilities, capability_total)}` "
            f"*{graduated_capabilities}/{capability_total}* ({capability_percent:g}%)\n"
            f"Frontier: *{public_text(semantics.get('current_execution_frontier') or 'Not established')}*",
        ),
        (
            "Action Effectiveness",
            f"Effective assignments: *{int(effectiveness.get('effective_assignments') or 0)}/"
            f"{int(effectiveness.get('assignments_evaluated') or 0)}* "
            f"({float(effectiveness.get('effectiveness_percent') or 100):g}%)\n"
            f"Suppressed unchanged actions: *{int(effectiveness.get('suppressed_fingerprints') or 0)}* | "
            f"State contract defects: *{int(effectiveness.get('state_model_defects') or 0)}*",
        ),
        (
            "Milestone Graduation",
            f"Milestone proof `{progress_bar(verified_milestones, len(milestones))}` "
            f"*{verified_milestones}/{len(milestones)} verified*\n"
            + "\n".join(milestone_lines),
        ),
        (
            "Milestone Debt, Risk, Confidence & Forecast",
            f"Graduation debt: *{milestone_debts}* | Program risk: "
            f"*{public_text(program_risk.get('level') or 'unknown')} ({int(program_risk.get('score') or 0)}/100)*\n"
            f"Operator confidence: *{operator_confidence:g}%* | Forecast: "
            f"*{str(forecast_days) + ' days' if forecast_days is not None else 'insufficient history'}*",
        ),
        (
            "Work In Progress",
            f"Capacity `{progress_bar(wip_total, wip_limit)}` *{wip_total}/{wip_limit} active*\n"
            f"Engineering {int(wip_counts.get('implementation') or 0)} | Review {int(wip_counts.get('integration') or 0)} | Validation {int(wip_counts.get('verification') or 0)}",
        ),
        (
            "Recent Activity",
            "\n".join(recent)
            + f"\n• Routine unchanged evidence checks: *{routine_no_ops}* (dashboard only)",
        ),
        (
            "Current Constraint",
            f"*{public_text(constraint.get('name') or scheduler.get('limiting_constraint') or 'None observed')}*\n"
            f"Evidence: {public_text('; '.join(constraint.get('evidence') or ['No blocking proof']))}\n"
            f"Action: {public_text(constraint.get('recommended_action') or 'Continue the verified roadmap')}",
        ),
        (
            "Engineering Progress",
            f"Verified engineering `{progress_bar(verified, total)}` *{verified}/{total}*\n"
            f"Ready roadmap items: *{int((semantics.get('supervisor_work') or {}).get('ready_work_item_count') or 0)}*",
        ),
        (
            "Deployment Rings",
            f"Runtime deployment `{progress_bar(deployed, runtime_total)}` *{deployed}/{runtime_total} current*\n"
            f"Pending deployment plans: *{len(capabilities.get('deployment_assignments') or [])}*",
        ),
        (
            "Runtime Validation",
            f"Validation proof `{progress_bar(validated, runtime_total)}` *{validated}/{runtime_total} verified*\n"
            "Deployment state and validation proof are reported separately.",
        ),
        (
            "Capability Graduation",
            f"Graduation proof `{progress_bar(graduated_capabilities, capability_total)}` "
            f"*{graduated_capabilities}/{capability_total} graduated*\n"
            f"Promotion: {public_text((capabilities.get('promotion_status') or {}).get('reason') or 'No capability projection available')}",
        ),
        (
            "Ghost Runtime",
            f"{ghost_icon} *{ghost_status}* | Capability gaps: *{host_capability(ghost)}*",
        ),
        (
            "Ghost Web",
            f"{ghost_web_icon} *{ghost_web_status}* | Web presentation proof is tracked independently from the runtime.",
        ),
        (
            "Nyx axis-node",
            f"{nyx_icon} *{nyx_status}* | axis-node: "
            f"*{'available' if (nyx or {}).get('required_command_available') else 'not verified'}* | Capability gaps: *{host_capability(nyx)}*",
        ),
        (
            "macbookpro axis-desktop",
            f"{mac_icon} *{mac_status}* | axis-desktop: "
            f"*{'available' if (macbookpro or {}).get('required_command_available') else 'not verified'}* | Capability gaps: *{host_capability(macbookpro)}*",
        ),
        (
            "mbair axis-desktop",
            f"{mbair_icon} *{mbair_status}* | axis-desktop validation is intentionally gray while the host is offline.",
        ),
        (
            "Human Action Required",
            "*Yes* — "
            + ", ".join(f"`{public_text(value)}`" for value in pending_decisions)
            if pending_decisions
            else "*Yes* — review the current authority gate"
            if need_human
            else "*No* — execution can continue within current authority",
        ),
    ]
    if tuple(title for title, _text in sections) != DASHBOARD_PROOF_SECTIONS:
        raise ValueError("executive dashboard proof section contract changed")

    fallback = (
        f"AXIS Executive Dashboard | Graduated capabilities {graduated_capabilities}/{capability_total} "
        f"({capability_percent:g}%) | Roadmap {verified}/{total} verified ({roadmap_percent}%) | "
        f"Action effectiveness {float(effectiveness.get('effectiveness_percent') or 100):g}% | "
        f"Milestones {verified_milestones}/{len(milestones)} | WIP {wip_total}/{wip_limit} | "
        f"Deployment {deployed}/{runtime_total} | Validation {validated}/{runtime_total} | "
        f"Human action {'yes' if need_human else 'no'}"
    )
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "AXIS Executive Dashboard"},
        },
        *[
            {
                "type": "section",
                "block_id": "axis_"
                + re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_"),
                "text": {"type": "mrkdwn", "text": f"*{title}*\n{text}"},
            }
            for title, text in sections
        ],
    ]
    rendered = json.dumps({"fallback": fallback, "blocks": blocks}, sort_keys=True)
    fingerprint = hashlib.sha256(rendered.encode()).hexdigest()
    return fallback, blocks, fingerprint
