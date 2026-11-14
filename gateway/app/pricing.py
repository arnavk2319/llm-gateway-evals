from __future__ import annotations

from .policy import ModelSpec


def list_cost(spec: ModelSpec, input_tokens: int, output_tokens: int) -> float:
    p = spec.price
    return input_tokens / 1_000_000 * p.input_per_1m + output_tokens / 1_000_000 * p.output_per_1m


def compute_cost(spec: ModelSpec, input_tokens: int, output_tokens: int) -> tuple[float, float]:
    """Returns (billed_cost, shadow_cost).

    Free models bill 0 but still report a shadow cost at list price, so cost
    metrics and routing stay meaningful before any paid model is in use.
    """
    shadow = list_cost(spec, input_tokens, output_tokens)
    return (0.0 if spec.free else shadow), shadow
