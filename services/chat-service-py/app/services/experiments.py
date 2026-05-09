"""Deterministic A/B test framework with sticky bucketing.

A user's variant is decided by `sha256(wallet + experiment_name) mod 100`,
so the same wallet always sees the same variant within an experiment —
critical for chat continuity (a user can't get the gpt-5.4-nano summarizer
on Monday and the claude-haiku-4-5 one on Tuesday for the same conversation
without confusing the responder).

Usage:

    from app.services.experiments import variant

    chosen = variant(wallet, "summarizer_model")
    # chosen is one of "control" / "treatment_a" / "treatment_b" — string-keyed
    if chosen == "treatment_a":
        model = "claude-haiku-4-5"
    else:
        model = settings.OPRAI_SUMMARIZER_MODEL

Wired-in experiments are declared in `_EXPERIMENTS`. Each has a name, a
list of (variant_name, weight) pairs that must sum to 100. Adding an
experiment is one dict entry; rolling it out is bumping the treatment
weight from 10 → 50 → 100 with no code changes elsewhere.

Why hash-based sticky bucketing instead of a database?
    * Zero DB round-trips per request.
    * Survives DB restarts; deterministic across deploys.
    * No need to backfill old wallets when an experiment ships.

The downside is we can't change a user's variant mid-experiment without
moving them. That's a feature, not a bug — variant flapping ruins eval
signal. To intentionally re-bucket, change the experiment name or salt.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Iterable

from prometheus_client import Counter

logger = logging.getLogger(__name__)


# Each entry maps experiment name → list of (variant, weight). Weights are
# in [0, 100] and must sum to 100. Variant names should be stable: changing
# `treatment_a` to `treatment_a_v2` re-buckets every user even if the
# weights are unchanged, because the salt changes.
_EXPERIMENTS: dict[str, list[tuple[str, int]]] = {
    # Canonical example. Adjust weights before shipping a real experiment.
    # control: existing summarizer / claude-haiku is the treatment.
    "summarizer_model": [
        ("control", 100),
        ("treatment_haiku", 0),
    ],
    # Output validator second-pass: control = always run; treatment skips
    # for low-cost queries (price/portfolio reads) to claw back latency.
    "validator_skip_lowcost": [
        ("control", 100),
        ("treatment_skip", 0),
    ],
    # Robust price fallback: control = single-source (existing behaviour);
    # treatment routes price reads through clients/multi_source.price_robust.
    "price_robust_default": [
        ("control", 100),
        ("treatment_robust", 0),
    ],
}


EXPERIMENT_ASSIGNMENTS = Counter(
    "chat_service_experiment_assignments_total",
    "How many requests landed in each experiment variant",
    ["experiment", "variant"],
)


def _bucket(wallet: str, experiment: str) -> int:
    """Deterministic 0–99 bucket for (wallet, experiment).

    Hash includes the experiment name so a wallet's bucket varies between
    experiments (otherwise every experiment would correlate its split).
    """
    h = hashlib.sha256(f"{wallet}:{experiment}".encode("utf-8")).digest()
    # Take first 4 bytes → uint32 → mod 100. Plenty of entropy for a
    # binary or ternary split.
    return int.from_bytes(h[:4], "big") % 100


def variant(wallet: str | None, experiment: str) -> str:
    """Resolve the user's variant for an experiment, with telemetry.

    Falls back to "control" if:
      * wallet is empty/anonymous (we can't sticky-bucket)
      * experiment name is unknown (typo / removed)
      * the weights don't sum to 100 (config error — log loudly)
    """
    if not wallet:
        EXPERIMENT_ASSIGNMENTS.labels(experiment=experiment, variant="control").inc()
        return "control"

    config = _EXPERIMENTS.get(experiment)
    if not config:
        logger.warning("experiments: unknown experiment %r — falling back to control", experiment)
        EXPERIMENT_ASSIGNMENTS.labels(experiment=experiment, variant="control").inc()
        return "control"

    if sum(weight for _, weight in config) != 100:
        logger.error("experiments: %r weights don't sum to 100 — falling back to control", experiment)
        EXPERIMENT_ASSIGNMENTS.labels(experiment=experiment, variant="control").inc()
        return "control"

    bucket = _bucket(wallet, experiment)
    cursor = 0
    for v_name, weight in config:
        cursor += weight
        if bucket < cursor:
            EXPERIMENT_ASSIGNMENTS.labels(experiment=experiment, variant=v_name).inc()
            return v_name

    # Mathematically unreachable when weights sum to 100, but keep a safe default.
    EXPERIMENT_ASSIGNMENTS.labels(experiment=experiment, variant="control").inc()
    return "control"


def is_variant(wallet: str | None, experiment: str, target_variant: str) -> bool:
    """Sugar for `variant(...) == target_variant`. Saves callers a string compare."""
    return variant(wallet, experiment) == target_variant


def list_experiments() -> Iterable[str]:
    """Names of registered experiments (for /admin or /metrics introspection)."""
    return _EXPERIMENTS.keys()
