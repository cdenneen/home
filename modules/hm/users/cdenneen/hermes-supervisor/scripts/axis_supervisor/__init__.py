"""Host-local external Development Supervisor components."""

from typing import Any


def progress_coherence(
    inventory: dict[str, Any],
    graph: dict[str, Any],
    graduation: dict[str, Any],
    mission: dict[str, Any],
) -> dict[str, Any]:
    """Return whether progress records describe one authoritative snapshot."""
    inventory_generation = inventory.get("generation_id")
    graph_generation = graph.get("generation_id")
    graduation_digest = graduation.get("projection_digest")
    convergence_digest = graduation.get("source_convergence_digest")
    mission_sources = mission.get("source_generations") or {}
    checks = {
        "graph_inventory": (
            graph.get("inventory_generation_id"),
            inventory_generation,
        ),
        "graduation_inventory": (
            graduation.get("source_inventory_generation_id"),
            inventory_generation,
        ),
        "graduation_graph": (
            graduation.get("source_graph_generation_id"),
            graph_generation,
        ),
        "mission_inventory": (mission_sources.get("inventory"), inventory_generation),
        "mission_graph": (mission_sources.get("graph"), graph_generation),
        "mission_graduation": (
            mission_sources.get("graduation"),
            graduation_digest,
        ),
        "mission_convergence": (
            mission_sources.get("convergence"),
            convergence_digest,
        ),
    }
    failures = [
        name
        for name, (actual, expected) in checks.items()
        if actual is None or expected is None or actual != expected
    ]
    return {
        "trusted": not failures,
        "failures": failures,
        "checks": {
            name: {"actual": actual, "expected": expected}
            for name, (actual, expected) in checks.items()
        },
    }
