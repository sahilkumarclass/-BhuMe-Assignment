#!/usr/bin/env python3
"""
Correct a village's shifted cadastral boundaries -> predictions.geojson.

    uv run solve.py data/34855_vadnerbhairav_chandavad_nashik

Reads the village bundle (plots + imagery + optional boundary hints), runs the image-based
alignment method in `solver/`, writes a contract-valid `predictions.geojson` beside the input, and
— if `example_truths.geojson` is present — prints the local scorecard so you can see the lift over
the naive baseline. The method never uses the truths, so the same code runs on the hidden set.
"""

from __future__ import annotations

import sys
from pathlib import Path

from bhume import load, score, write_predictions
from solver import correct_village

DEFAULT_VILLAGE = 'data/34855_vadnerbhairav_chandavad_nashik'


def main(village_dir: str, max_plots: int | None = None) -> None:
    village = load(village_dir)
    print(f'Loaded {village.slug}: {len(village.plots)} plots · '
          f'boundaries={"yes" if village.boundaries_path else "none"}')

    preds = correct_village(village, max_plots=max_plots)
    n_corr = int((preds['status'] == 'corrected').sum())
    n_flag = int((preds['status'] == 'flagged').sum())
    print(f'  village drift prior: dx={preds.attrs.get("prior_dx", 0):.1f}m '
          f'dy={preds.attrs.get("prior_dy", 0):.1f}m')
    print(f'  decided: {n_corr} corrected · {n_flag} flagged')

    out = write_predictions(Path(village_dir) / 'predictions.geojson', preds)
    print(f'  wrote {len(preds)} predictions -> {out}')

    if village.example_truths is not None:
        print()
        print(score(preds, village))
    else:
        print('\n(no example_truths.geojson — download it into the village folder to self-score)')


if __name__ == '__main__':
    args = sys.argv[1:]
    vdir = args[0] if args else DEFAULT_VILLAGE
    mp = int(args[1]) if len(args) > 1 else None
    main(vdir, mp)
