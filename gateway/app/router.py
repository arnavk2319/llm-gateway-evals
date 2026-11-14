from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

from .policy import ModelSpec, Policy
from .pricing import list_cost
from .profile import TaskProfile


@dataclass
class ParentRef:
    model_id: str
    family: str


@dataclass
class RouteDecision:
    primary: ModelSpec
    fallbacks: list[ModelSpec]
    reason: str
    relaxed: list[str] = field(default_factory=list)
    candidates_considered: int = 0
    scores: dict[str, float] = field(default_factory=dict)


def _capable(m: ModelSpec, profile: TaskProfile, stream: bool) -> bool:
    c = m.capabilities
    if profile.needs_tools and not c.tools:
        return False
    if profile.needs_json and not c.json_mode:
        return False
    if stream and not c.streaming:
        return False
    return profile.context_tokens + profile.expected_output_tokens <= c.max_context


def select_route(
    policy: Policy,
    profile: TaskProfile,
    *,
    node_role: str = "unknown",
    parent: ParentRef | None = None,
    excluded: set[str] | None = None,
    escalate_tiers: int = 0,
    pinned: str | None = None,
    stream: bool = False,
    env: Mapping[str, str] | None = None,
) -> RouteDecision | None:
    """Pure routing function: capability filter, constraints, rank.

    Returns None when no candidate is left.
    """
    env = os.environ if env is None else env
    excluded = excluded or set()
    avail = policy.available_models(env)

    if pinned:
        spec = next((m for m in avail if m.id == pinned), None)
        if spec is None:
            return None
        return RouteDecision(spec, [], f"pinned by request: {spec.id}", [], 1, {})

    relaxed: list[str] = []
    cands = [m for m in avail if m.id not in excluded and _capable(m, profile, stream)]
    if not cands:
        return None

    tp = policy.task_policy(profile.task_type)
    want_tier = tp.min_tier + (1 if profile.complexity == "high" else 0) + escalate_tiers
    max_tier = max(m.tier for m in cands)
    eff_tier = min(want_tier, max_tier)
    if eff_tier < want_tier:
        relaxed.append("tier")
    pool = [m for m in cands if m.tier >= eff_tier]

    rc = policy.role_constraints.get(node_role)
    if rc and rc.independence == "family" and parent is not None:
        other_family = [m for m in pool if m.family != parent.family]
        if other_family:
            pool = other_family
        else:
            any_other_family = [m for m in cands if m.family != parent.family]
            if any_other_family:
                pool = any_other_family
                relaxed.append("tier-for-independence")
            else:
                other_model = [m for m in pool if m.id != parent.model_id]
                if other_model:
                    pool = other_model
                    relaxed.append("independence:family->model")
                else:
                    relaxed.append("independence:none")

    in_tok, out_tok = profile.context_tokens, profile.expected_output_tokens
    # Ranking uses list price so free models are compared on realistic cost.
    costs = {m.id: list_cost(m, in_tok, out_tok) for m in pool}
    max_cost = max(costs.values()) or 1.0
    max_lat = max(m.latency_ms_p50 for m in pool) or 1.0
    w = policy.weights
    wq = w.quality * (1.5 if profile.risk == "high" else 1.0)

    scores = {
        m.id: wq * m.quality_for(profile.task_type)
        - w.cost * costs[m.id] / max_cost
        - w.latency * m.latency_ms_p50 / max_lat
        for m in pool
    }
    ranked = sorted(pool, key=lambda m: (-scores[m.id], m.id))
    primary, fallbacks = ranked[0], ranked[1:]
    reason = (
        f"task={profile.task_type} complexity={profile.complexity} tier>={eff_tier}; "
        f"picked {primary.id} (score {scores[primary.id]:.3f})"
    )
    if fallbacks:
        reason += f", next {fallbacks[0].id} ({scores[fallbacks[0].id]:.3f})"
    if relaxed:
        reason += f"; relaxed: {', '.join(relaxed)}"
    return RouteDecision(primary, fallbacks, reason, relaxed, len(pool), scores)
