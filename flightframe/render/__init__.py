"""Poster designs.

The registry below is the single source of truth for what designs exist. The
CLI and the web gallery both read it. They used to keep their own lists, which
drifted the moment a fifth design was added — the gallery simply never showed
it, with no error anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import flight, liveried, next as next_flight, portrait, rose, section  # noqa: F401


@dataclass(frozen=True)
class Design:
    name: str
    title: str
    blurb: str
    needs_history: bool = False
    on_demand: bool = False        # rendered only when there is something to show


DESIGNS: tuple[Design, ...] = (
    Design("flight", "Tracked flight",
           "One flight followed to its destination", on_demand=True),
    Design("rose", "Destination rose",
           "One spoke per destination, at its true bearing and distance",
           needs_history=True),
    Design("section", "Cross-section",
           "Distance from home against altitude, right now"),
    Design("portrait", "Single plane",
           "One aircraft, chosen by a rotating superlative"),
    Design("liveried", "Liveried grid",
           "Four aircraft on a flooded background"),
    Design("next", "Flying next",
           "Where you are flying next: countdown, route, aircraft"),
)

NAMES: tuple[str, ...] = tuple(d.name for d in DESIGNS)
BY_NAME: dict[str, Design] = {d.name: d for d in DESIGNS}
