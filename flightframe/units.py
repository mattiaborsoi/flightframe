"""Units, so the posters can speak kilometres instead of nautical miles.

The APIs are aviation-native: adsb.lol reports distance in nautical miles,
altitude in feet, speed in knots. That is the right thing on the wire and the
wrong thing on a wall in London, so conversion happens once, at render time.

`AVIATION` is kept because flight levels genuinely are how the sky is
organised, and a cross-section labelled FL100/200/300 is more truthful to what
it depicts. Set UNITS=aviation in .env to get it back.
"""
from __future__ import annotations

from dataclasses import dataclass

NM_TO_KM = 1.852
FT_TO_M = 0.3048


@dataclass(frozen=True)
class Units:
    name: str
    distance_suffix: str
    altitude_suffix: str
    speed_suffix: str

    # -- conversion -------------------------------------------------------

    def distance(self, nm: float) -> float:
        return nm * NM_TO_KM if self.name == "metric" else nm

    def altitude(self, ft: float) -> float:
        return ft * FT_TO_M if self.name == "metric" else ft

    def speed(self, kt: float) -> float:
        return kt * NM_TO_KM if self.name == "metric" else kt

    def from_km(self, km: float) -> float:
        """A value already in kilometres, expressed in the display unit.

        Distinct from distance(), which takes nautical miles. Route lengths are
        computed and stored in km; passing one to distance() multiplies by
        1.852 a second time, which is how Guam ended up 22,275 km from London —
        further than any two points on Earth can be.
        """
        return km if self.name == "metric" else km / NM_TO_KM

    # -- formatting -------------------------------------------------------

    def distance_str(self, nm: float, decimals: int = 1) -> str:
        return f"{self.distance(nm):,.{decimals}f} {self.distance_suffix}"

    def altitude_str(self, ft: float) -> str:
        return f"{self.altitude(ft):,.0f} {self.altitude_suffix}"

    def speed_str(self, kt: float) -> str:
        return f"{self.speed(kt):,.0f} {self.speed_suffix}"

    def climb_str(self, fpm: float) -> str:
        """Vertical rate, per minute."""
        v = abs(fpm) * (FT_TO_M if self.name == "metric" else 1)
        return f"{v:,.0f} {self.altitude_suffix}/min"

    def ring_steps(self, radius_nm: float) -> list[float]:
        """Sensible ring/gridline distances, in display units."""
        span = self.distance(radius_nm)
        step = 10 if span <= 60 else 20 if span <= 150 else 50
        if self.name == "aviation":
            step = 5 if span <= 30 else 10
        out, v = [], float(step)
        while v < span:
            out.append(v)
            v += step
        return out

    def to_nm(self, value: float) -> float:
        """Display units back to nautical miles, for projecting a ring."""
        return value / NM_TO_KM if self.name == "metric" else value

    def altitude_steps(self, ceiling_ft: float) -> list[tuple[float, str]]:
        """Altitude gridlines as (feet, label)."""
        if self.name == "aviation":
            return [(ft, f"{int(ft // 100)}") for ft in (10_000, 20_000, 30_000, 40_000)]
        out = []
        m = 3_000
        while m <= self.altitude(ceiling_ft):
            out.append((m / FT_TO_M, f"{m // 1000:,.0f}"))
            m += 3_000
        return out


METRIC = Units("metric", "km", "m", "km/h")
AVIATION = Units("aviation", "nm", "ft", "kt")

BY_NAME = {"metric": METRIC, "aviation": AVIATION}


def get(name: str | None) -> Units:
    return BY_NAME.get((name or "metric").strip().lower(), METRIC)
