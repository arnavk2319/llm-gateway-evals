from __future__ import annotations

import os
import random
from collections.abc import Mapping
from dataclasses import dataclass, field

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
    allowed: set[str] | None = None,
    unhealthy: set[str] | None = None,
    sensitive: bool = False,
    saturated: set[str] | None = None,
    load: Mapping[str, float] | None = None,
    rng: random.Random | None = None,
) -> RouteDecision | None:
    """Pure routing function: capability filter, constraints, rank.

    `allowed` restricts candidates to the models a caller's key may use.
    `unhealthy` is the set of providers whose circuit is open; they are skipped
    unless that would leave no candidate.
    `sensitive` restricts candidates to providers cleared for sensitive content. This is eligibility,
    not preference: it is never relaxed, and it applies to a pinned model too.
    `saturated` is the set of providers at their concurrency or rate limit. Like eligibility it is a hard limit:
    sending more would break it. `load` (0 idle .. 1 full, by provider) tilts the choice among near-equal models
    toward the less busy provider, and `rng` makes that choice reproducible.

    Returns None when no candidate is left.
    """
    env = os.environ if env is None else env
    excluded = excluded or set()
    avail = policy.available_models(env)
    if allowed is not None:
        avail = [m for m in avail if m.id in allowed]
    if sensitive:
        avail = [m for m in avail if policy.providers[m.provider].sensitive_ok]

    if pinned:
        spec = next((m for m in avail if m.id == pinned), None)
        if spec is None or (saturated and spec.provider in saturated):
            return None
        return RouteDecision(spec, [], f"pinned by request: {spec.id}", [], 1, {})

    relaxed: list[str] = []
    cands = [m for m in avail if m.id not in excluded and _capable(m, profile, stream)]
    if not cands:
        return None
    if unhealthy:
        healthy = [m for m in cands if m.provider not in unhealthy]
        if healthy:
            cands = healthy
        else:
            relaxed.append("circuit-open:ignored")
    if saturated:
        # After the health step on purpose: when the healthy providers are merely full, the answer is "busy, retry
        # shortly", not "send it to the one whose circuit is open".
        cands = [m for m in cands if m.provider not in saturated]
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
    spread = 1
    bal = policy.balance
    if bal.enabled and bal.margin > 0:
        top = scores[ranked[0].id]
        near = [m for m in ranked if top - scores[m.id] <= bal.margin]
        if len(near) > 1:
            def weight(m: ModelSpec) -> float:
                closeness = 1.0 - 0.5 * (top - scores[m.id]) / bal.margin  # 1.0 at the best, 0.5 at the margin
                idle = 1.0 - min(0.9, (load or {}).get(m.provider, 0.0)) if bal.load_aware else 1.0
                return closeness * idle

            chooser = rng or random
            primary = chooser.choices(near, weights=[weight(m) for m in near], k=1)[0]
            fallbacks = [m for m in ranked if m is not primary]
            spread = len(near)
        if bal.diverse_fallback:  # a failover to the same provider fails the same way; try another source first
            fallbacks = [m for m in fallbacks if m.provider != primary.provider] + [
                m for m in fallbacks if m.provider == primary.provider
            ]
    reason = (
        f"task={profile.task_type} complexity={profile.complexity} tier>={eff_tier}; "
        f"picked {primary.id} (score {scores[primary.id]:.3f})"
        + (f", spread across {spread} near-equal models" if spread > 1 else "")
    )
    if fallbacks:
        reason += f", next {fallbacks[0].id} ({scores[fallbacks[0].id]:.3f})"
    if relaxed:
        reason += f"; relaxed: {', '.join(relaxed)}"
    return RouteDecision(primary, fallbacks, reason, relaxed, len(pool), scores)
