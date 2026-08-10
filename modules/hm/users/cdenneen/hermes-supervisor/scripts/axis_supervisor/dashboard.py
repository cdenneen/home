import hashlib
import json
import re
from pathlib import Path

from . import progress_coherence
from .capability_graduation import read_capability_graduation
from .decisions import DecisionStore
from .missions import read_mission_record
from .schema_registry import read_record

DASHBOARD_PROOF_SECTIONS = (
    "AXIS",
    "ROADMAP",
    "CAPABILITIES",
    "ACTIVE PRODUCT WORK",
    "DEPLOYMENT RING",
    "VALIDATION",
    "DECISIONS",
    "RECENT PRODUCT PROGRESS",
)

PRODUCT_CAPABILITIES = (
    ("CLI", "CLI"),
    ("Node", "Node Runtime"),
    ("Web", "Web Presentation"),
    ("Desktop", "Desktop Presentation"),
    ("HUD", "HUD"),
    ("Neural", "Neural Map"),
)

_INTERNAL_TERMS = (
    (r"\bissues?\b", "product item"),
    (r"\bassignments?\b", "product action"),
    (r"\bworktrees?\b", "workspace"),
    (r"\bleases?\b", "reservation"),
    (r"\bgrants?\b", "authorization"),
    (r"\benums?\b", "states"),
    (r"\bCI(?:[- ]poll(?:ing)?)?\b", "integration validation"),
    (r"\bmodel(?:-call)?\b", "execution"),
    (r"\baccounting\b", "metrics"),
    (r"\blifecycle\b", "delivery"),
    (r"\btimestamps?\b", "time"),
)


def public_text(value: object) -> str:
    text = str(value or "")
    for pattern, replacement in _INTERNAL_TERMS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text.strip()


def progress_bar(value: float, total: float, width: int = 12) -> str:
    bounded_total = max(0.0, float(total))
    bounded_value = (
        min(max(0.0, float(value)), bounded_total) if bounded_total else 0.0
    )
    filled = round(width * bounded_value / bounded_total) if bounded_total else 0
    return "█" * filled + "░" * (width - filled)


def _load(root: Path, name: str, schema: str) -> dict:
    path = root / name
    return read_record(path, schema) if path.exists() else {}


def _runtime_status(record: dict | None, *, offline: bool = False) -> str:
    if offline:
        return "⚪ Offline"
    if not record:
        return "⚪ Not observed"
    if (
        record.get("status") == "converged"
        and record.get("verification_status") == "verified"
    ):
        return "🟢 Verified"
    if record.get("status") == "unknown":
        return "🔴 Validation unavailable"
    return "🟡 " + public_text(record.get("status") or "Validation pending").title()


def _recent_lines(events: list[dict]) -> list[str]:
    labels = {
        "implementation_completed": "Engineering change prepared",
        "mr_created": "Engineering review opened",
        "mr_merged": "Engineering change integrated",
        "post_main_verified": "Mainline evidence verified",
        "capability_deployment_verified": "Product capability verified",
    }
    visible = []
    for event in reversed(events):
        label = labels.get(str(event.get("event_type") or ""))
        if not label:
            continue
        ref = public_text(event.get("work_item"))
        visible.append(f"• {label}" + (f" — `{ref}`" if ref else ""))
        if len(visible) == 4:
            break
    return visible or ["• No material product change in the current activity window"]


def _confidence(value: object) -> float:
    if not isinstance(value, (int, float, str)):
        return 0.0
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def confidence_text(value: object) -> str:
    return "N/A" if value is None else f"{_confidence(value):g}%"


def _capability_line(label: str, record: dict) -> str:
    production = _confidence(record.get("production_confidence"))
    operator = record.get("operator_confidence")
    operator_text = confidence_text(operator)
    state = "Graduated" if record.get("graduated") else public_text(
        record.get("first_failing_gate") or "Evidence pending"
    ).replace("_", " ").title()
    return (
        f"• *{label}* `{progress_bar(production, 100, 8)}` production {production:g}% | "
        f"operator {operator_text} | {state}"
    )


def _milestone_lines(semantics: dict, graduation: dict) -> list[str]:
    proof = {
        str(value.get("milestone")): value
        for value in graduation.get("milestones") or []
    }
    lines = []
    for milestone in semantics.get("complete_roadmap") or []:
        key = str(milestone.get("key") or "Unmapped")
        progress = milestone.get("progress") or {}
        current = proof.get(key) or {}
        production = _confidence(current.get("production_confidence"))
        operator = current.get("operator_confidence")
        operator_value = _confidence(operator)
        risk = current.get("program_risk") or {}
        debt = len(current.get("debts") or [])
        constraint = public_text(
            current.get("constraint")
            or milestone.get("zero_executable_reason")
            or "None observed"
        )
        lines.extend(
            [
                f"*{key}* — {public_text(milestone.get('status') or 'planned')}",
                f"Delivery   `{progress_bar(progress.get('count', 0), progress.get('denominator', 0), 10)}` "
                f"{int(progress.get('count') or 0)}/{int(progress.get('denominator') or 0)}",
                f"Production `{progress_bar(production, 100, 10)}` {production:g}% | "
                f"Operator `{progress_bar(operator_value, 100, 10)}` "
                + confidence_text(operator),
                f"Risk *{public_text(risk.get('level') or 'unknown')}* ({int(risk.get('score') or 0)}/100) | "
                f"Debt *{debt}* | Constraint: {constraint}",
            ]
        )
    return lines or ["• No milestone evidence is available"]


def render_executive_dashboard(
    root: Path,
    inventory: dict,
    graph: dict,
    semantics: dict,
    events: list[dict],
) -> tuple[str, list[dict], str]:
    graduation_path = root / "capability-graduation.json"
    graduation = (
        read_capability_graduation(graduation_path)
        if graduation_path.exists()
        else {}
    )
    convergence = _load(
        root,
        "capability-convergence.json",
        "axis.external-development-supervisor.capability-convergence",
    )
    mission_path = root / "active-mission.json"
    mission = read_mission_record(mission_path) if mission_path.exists() else {}
    coherence = progress_coherence(inventory, graph, graduation, mission)
    progress_notice = None
    if not coherence["trusted"]:
        progress_notice = (
            "⚠️ Supervisor progress state is reconciling; capability and mission "
            "claims are withheld until source generations agree ("
            + ", ".join(coherence["failures"])
            + ")."
        )
        graduation = {}
        mission = {}

    total = int(semantics.get("total_governed_items") or 0)
    verified = int(
        ((semantics.get("composition") or {}).get("verified_complete") or {}).get(
            "count"
        )
        or 0
    )
    primary = graduation.get("primary_kpi") or {}
    graduated = int(primary.get("count") or 0)
    capability_total = int(primary.get("denominator") or 0)
    capability_percent = _confidence(primary.get("percent"))
    production_confidence = _confidence(graduation.get("production_confidence"))
    operator_confidence = graduation.get("operator_confidence")
    risk = graduation.get("program_risk") or {}

    capability_by_name = {
        str(value.get("capability")): value
        for value in graduation.get("capabilities") or []
    }
    capability_lines = [
        _capability_line(label, capability_by_name.get(name) or {})
        for label, name in PRODUCT_CAPABILITIES
    ]

    actions = mission.get("generated_actions") or []
    active_lines = []
    for action in actions[:5]:
        capabilities = action.get("expected_capabilities") or []
        active_lines.append(
            f"• *{public_text(action.get('target') or 'Product work')}* — "
            f"{public_text(action.get('engineering_purpose') or action.get('reason'))} | "
            f"Capabilities: {', '.join(public_text(value) for value in capabilities) or 'cross-product'}"
        )
    if not active_lines:
        active_lines.append("• No active product change; evidence reconciliation may continue")

    runtimes = {
        str(value.get("runtime")): value for value in convergence.get("runtimes") or []
    }
    ghost = runtimes.get("ghost")
    ghost_web_verified = bool(
        ghost
        and ghost.get("verification_status") == "verified"
        and "Web Presentation" not in (ghost.get("capabilities_behind") or [])
    )
    required_deployments = [
        ghost,
        {"status": "converged", "verification_status": "verified"}
        if ghost_web_verified
        else {},
        runtimes.get("nyx"),
        runtimes.get("macbookpro"),
    ]
    required_verified = sum(
        bool(
            value
            and value.get("status") == "converged"
            and value.get("verification_status") == "verified"
        )
        for value in required_deployments
    )
    deployment_lines = [
        f"Required deployment `{progress_bar(required_verified, len(required_deployments), 8)}` "
        f"*{required_verified}/{len(required_deployments)} verified*",
        f"• *Ghost Runtime* — {_runtime_status(ghost)}",
        f"• *Web* — {'🟢 Verified' if ghost_web_verified else '🟡 Validation pending' if ghost else '⚪ Not observed'}",
        f"• *Nyx* — {_runtime_status(runtimes.get('nyx'))}",
        f"• *macbookpro* — {_runtime_status(runtimes.get('macbookpro'))}",
        f"• *mbair* — {_runtime_status(runtimes.get('mbair'), offline=True)} (optional)",
    ]

    validation_streams = graduation.get("validation_streams") or []
    validation_lines = [
        f"• *{public_text(value.get('title') or value.get('stream'))}* — "
        f"{public_text(value.get('status') or 'pending')} | "
        f"Evidence: `{public_text((value.get('evidence') or {}).get('uri') or 'not yet available')}`"
        for value in validation_streams
    ] or ["• No product validation evidence is available"]

    decision_store = DecisionStore(root)
    decisions = []
    for node in graph.get("nodes") or []:
        packet = (node.get("semantic_record") or {}).get("decision_packet")
        if not isinstance(packet, dict):
            continue
        decision_id = str(packet.get("decision_id") or node.get("ref") or "")
        if decision_store.load(decision_id) is not None:
            continue
        decisions.append(
            f"• `{public_text(decision_id)}` — {public_text(packet.get('decision_requested'))}"
        )
    decision_lines = decisions or ["• No Product Owner decision is pending"]

    sections = (
        (
            "AXIS",
            (progress_notice + "\n" if progress_notice else "")
            + f"Capabilities `{progress_bar(graduated, capability_total)}` *{graduated}/{capability_total} graduated* "
            f"({capability_percent:g}%)\nProduction confidence *{production_confidence:g}%* | "
            f"Operator confidence *{confidence_text(operator_confidence)}* | "
            f"Risk *{public_text(risk.get('level') or 'unknown')} ({int(risk.get('score') or 0)}/100)*\n"
            "Drill down: `!axis capabilities`, `!axis risk`",
        ),
        (
            "ROADMAP",
            f"Verified product outcomes `{progress_bar(verified, total)}` *{verified}/{total}*\n"
            + "\n".join(_milestone_lines(semantics, graduation))
            + "\nDrill down: `!axis milestones` or `!axis milestone AX-M4`",
        ),
        (
            "CAPABILITIES",
            "\n".join(capability_lines)
            + "\nDrill down: `!axis capability CLI` (also Node, Web, Desktop, HUD, Neural)",
        ),
        (
            "ACTIVE PRODUCT WORK",
            "\n".join(active_lines)
            + "\nDrill down to source-linked evidence with `!axis inspect group/project#id`",
        ),
        (
            "DEPLOYMENT RING",
            "\n".join(deployment_lines) + "\nDrill down: `!axis deployments`",
        ),
        (
            "VALIDATION",
            "\n".join(validation_lines) + "\nDrill down: `!axis validation`",
        ),
        (
            "DECISIONS",
            "\n".join(decision_lines) + "\nDrill down: `!axis decisions`",
        ),
        (
            "RECENT PRODUCT PROGRESS",
            "\n".join(_recent_lines(events)) + "\nDrill down: `!axis recent`",
        ),
    )
    if tuple(title for title, _text in sections) != DASHBOARD_PROOF_SECTIONS:
        raise ValueError("product dashboard section contract changed")

    fallback = (
        ("AXIS | Progress reconciling | " if progress_notice else "AXIS | ")
        + f"Capabilities {graduated}/{capability_total} graduated ({capability_percent:g}%) | "
        f"Roadmap {verified}/{total} verified | Production confidence {production_confidence:g}% | "
        f"Operator confidence {confidence_text(operator_confidence)} | "
        f"Risk {public_text(risk.get('level') or 'unknown')} | "
        f"Decisions {len(decisions)}"
    )
    blocks = [
        block
        for title, text in sections
        for block in (
            {
                "type": "header",
                "text": {"type": "plain_text", "text": title},
            },
            {
                "type": "section",
                "block_id": "axis_"
                + re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_"),
                "text": {"type": "mrkdwn", "text": text},
            },
        )
    ]
    rendered = json.dumps({"fallback": fallback, "blocks": blocks}, sort_keys=True)
    return fallback, blocks, hashlib.sha256(rendered.encode()).hexdigest()
