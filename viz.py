#!/usr/bin/env python3
"""
Save before/after overlays for a few plots, to eyeball correction quality (and for the video).

    uv run viz.py data/34855_vadnerbhairav_chandavad_nashik

Draws the official outline (red) and our predicted outline (green) on the satellite patch for a
sample of plots, into out/overlays/. Run `solve.py` first so predictions.geojson exists.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from bhume import load
from bhume.geo import geom_to_imagery_crs, open_imagery, patch_for_plot
from bhume.io import read_predictions

PAD_M = 55.0


def _to_px(geom_img, transform):
    inv = ~transform
    polys = geom_img.geoms if geom_img.geom_type.startswith('Multi') else [geom_img]
    rings = []
    for p in polys:
        xs, ys = p.exterior.xy
        rings.append([inv * (x, y) for x, y in zip(xs, ys)])
    return rings


def main(village_dir: str, n: int = 12) -> None:
    v = load(village_dir)
    preds = read_predictions(Path(village_dir) / 'predictions.geojson')
    outdir = Path('out/overlays')
    outdir.mkdir(parents=True, exist_ok=True)

    # a spread: highest-confidence corrected, some flagged, some mid
    corr = preds[preds.status == 'corrected'].sort_values('confidence', ascending=False)
    flag = preds[preds.status == 'flagged']
    picks = list(corr.index[:n // 2]) + list(corr.index[len(corr) // 2:][:n // 4]) + list(flag.index[:n // 4])

    with open_imagery(v.imagery_path) as src:
        for pn in picks:
            official = v.plots.loc[pn, 'geometry']
            pred = preds.loc[pn, 'geometry']
            patch = patch_for_plot(src, official, pad_m=PAD_M)
            img = Image.fromarray(patch.image).convert('RGB').resize(
                (patch.image.shape[1] * 3, patch.image.shape[0] * 3), Image.NEAREST)
            draw = ImageDraw.Draw(img)
            for geom, color in ((official, (255, 60, 60)), (pred, (60, 255, 90))):
                for ring in _to_px(geom_to_imagery_crs(src, geom), patch.transform):
                    draw.line([(x * 3, y * 3) for x, y in ring] + [(ring[0][0] * 3, ring[0][1] * 3)],
                              fill=color, width=2)
            status = str(preds.loc[pn, 'status'])
            conf = preds.loc[pn, 'confidence']
            img.save(outdir / f'{status}_{pn}_conf{conf:.2f}.png')
    print(f'saved {len(picks)} overlays -> {outdir}  (red=official, green=predicted)')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'data/34855_vadnerbhairav_chandavad_nashik')
