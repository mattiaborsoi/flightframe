"""Poster designs.

The registry below is the single source of truth for what designs exist. The
CLI and the web gallery both read it. They used to keep their own lists, which
drifted the moment a fifth design was added — the gallery simply never showed
it, with no error anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import flight, next as next_flight  # noqa: F401


@dataclass(frozen=True)
class Design:
    name: str
    title: str
    blurb: str
    on_demand: bool = False        # rendered only when there is something to show


# The four designs that drew the sky overhead — destination rose,
# cross-section, single plane, liveried grid — were removed in Sept 2026.
# Nobody was looking at them, and between them they were the only reason
# the service watched live traffic at all: a collector polling adsb.lol
# round the clock, a position database per tenant, and the pruning to keep
# it from growing. What remains is a frame about the flights you are
# actually taking.
DESIGNS: tuple[Design, ...] = (
    Design("flight", "Tracked flight",
           "One flight followed to its destination", on_demand=True),
    Design("next", "Flying next",
           "Where you are flying next: countdown, route, aircraft"),
)

NAMES: tuple[str, ...] = tuple(d.name for d in DESIGNS)
BY_NAME: dict[str, Design] = {d.name: d for d in DESIGNS}
