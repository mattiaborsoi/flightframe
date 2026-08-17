"""Which poster the frame is currently showing.

Deliberately a single stored choice rather than a rotation. The designs are not
equally good at all hours — the trace is nearly empty at 07:00, the rose has
almost nothing in it before the morning bank — so cycling blindly guarantees
the frame is regularly showing a poster at its worst. Picking one and changing
it when you feel like it always shows something worth looking at.

A tracked flight overrides the choice entirely and reverts when it expires,
which is why `effective()` exists and callers should use it rather than
reading `current()` directly.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .render import BY_NAME, NAMES

DEFAULT = "trace"
FLIGHT = "flight"


class Selection:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "display.json"

    def current(self) -> str:
        """The design the owner chose, ignoring any tracked flight."""
        try:
            name = json.loads(self.path.read_text(encoding="utf-8")).get("design")
        except (OSError, json.JSONDecodeError, AttributeError):
            return DEFAULT
        return name if name in BY_NAME and name != FLIGHT else DEFAULT

    def set(self, name: str) -> tuple[bool, str]:
        name = (name or "").strip().lower()
        if name == FLIGHT:
            return False, "Use the flight tracker to show a flight."
        if name not in BY_NAME:
            return False, f"Unknown design. Choose one of: {', '.join(self.selectable())}."
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"design": name}), encoding="utf-8")
        os.replace(tmp, self.path)
        return True, f"Frame set to {BY_NAME[name].title}."

    @staticmethod
    def selectable() -> list[str]:
        """Everything a person can choose. `flight` appears on its own terms."""
        return [n for n in NAMES if n != FLIGHT]

    def effective(self, tracking_active: bool) -> str:
        """What the frame should actually show right now."""
        return FLIGHT if tracking_active else self.current()
