"""Turning alignment signals into a confidence and a correct/flag decision.

Confidence is the signal the grader weighs most, so it must *mean* something: it should track the
probability that a fix actually lands on the field (IoU with the hidden truth >= 0.5). We build it
only from truth-free evidence, so it generalizes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


@dataclass
class Decision:
    status: str          # "corrected" | "flagged"
    confidence: float    # [0, 1], meaningful for corrected rows
    snap_zero: bool      # True => keep the official position (already aligned / restraint)
    note: str


def area_ratio(map_area, recorded_sqm, pot_kharaba_ha):
    """Drawn area ÷ total recorded extent (cultivable + pot-kharaba). None if nothing on file."""
    if recorded_sqm is None or (isinstance(recorded_sqm, float) and math.isnan(recorded_sqm)):
        return None
    total = float(recorded_sqm)
    if pot_kharaba_ha is not None and not (isinstance(pot_kharaba_ha, float) and math.isnan(pot_kharaba_ha)):
        total += float(pot_kharaba_ha) * 10_000.0
    if total <= 0:
        return None
    return float(map_area) / total


# decision thresholds (deliberately conservative; the cost of a wrong "corrected" is high)
MIN_SHIFT_M = 2.5        # below this we treat the plot as already in place (don't move it)
AREA_LO, AREA_HI = 0.55, 1.80   # outside this, geometry disagrees with the record -> flag
WEAK_SHARP = 2.0         # peak barely above the noise floor
WEAK_SUPPORT = 0.12      # almost no detected edge under the moved outline


def assess(offset_m: float, sharpness: float, support: float, support_before: float,
           prior_dist_m: float, ratio: float | None) -> Decision:
    """Combine the signals into a confidence and a correct/flag/keep decision."""
    # --- individual evidence terms, each in [0, 1] ---
    s_sharp = _sigmoid((sharpness - 4.0) / 1.5)
    s_support = min(1.0, support / 0.45)
    s_gain = _sigmoid((support - support_before) / 0.08)          # did moving actually help?
    s_area = math.exp(-((ratio - 1.0) / 0.35) ** 2) if ratio is not None else 0.5
    s_prior = math.exp(-((prior_dist_m / 18.0) ** 2))             # agrees with village drift?

    conf = (0.30 * s_sharp + 0.28 * s_support + 0.14 * s_gain +
            0.14 * s_area + 0.14 * s_prior)
    conf = max(0.05, min(0.98, conf))

    # --- restraint: estimated drift is tiny and the outline already sits on edges -> leave it ---
    if offset_m < MIN_SHIFT_M and support_before >= 0.18:
        return Decision('corrected', max(conf, 0.55), snap_zero=True,
                        note=f'already aligned (offset {offset_m:.1f}m); kept official position')

    # --- flag: the geometry itself disagrees with the record; moving it won't help ---
    if ratio is not None and (ratio < AREA_LO or ratio > AREA_HI):
        return Decision('flagged', conf, snap_zero=False,
                        note=f'area mismatch (drawn/recorded={ratio:.2f}); shape problem, not placement')

    # --- flag: no trustworthy edge signal to align to (canopy / buildings / blank hints) ---
    if sharpness < WEAK_SHARP and support < WEAK_SUPPORT:
        return Decision('flagged', conf, snap_zero=False,
                        note='no confident field edge to align to (weak peak, low edge support)')

    return Decision('corrected', conf, snap_zero=False,
                    note=f'shifted {offset_m:.0f}m onto field edge (sharp={sharpness:.1f}, support={support:.2f})')
