"""The method end to end: estimate each plot's drift, learn a village prior, decide, build geometry.

Two passes over the plots:
  1) align every plot's outline to the imagery edges -> a raw (dx, dy) offset + quality signals;
  2) from the *confident* offsets, learn a robust village-wide drift prior, then finalize each
     plot — weak alignments fall back toward the prior, and we set confidence + correct/flag.

Pass 1 does the image work; pass 2 is pure arithmetic, so re-deciding (e.g. tuning confidence) is
cheap. Nothing here reads the example truths — the method must stand on its own to generalize.
"""

from __future__ import annotations

import math

import geopandas as gpd
import numpy as np
from pyproj import Transformer
from shapely.affinity import translate
from shapely.ops import transform as shp_transform
from shapely.validation import make_valid

from bhume.geo import geom_to_imagery_crs, open_imagery, patch_for_plot
from solver import confidence as conf
from solver import imageproc as ip

PAD_M = 55.0          # patch border; must exceed the largest drift we expect to recover
SEARCH_M = 40.0       # half-width of the offset search window


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values)
    v, w = values[order], weights[order]
    cdf = np.cumsum(w)
    if cdf[-1] <= 0:
        return float(np.median(values))
    return float(v[np.searchsorted(cdf, 0.5 * cdf[-1])])


def _align_plot(src, b_src, geom_4326):
    """Image work for one plot: returns (Alignment, patch.transform) or None if unusable."""
    try:
        patch = patch_for_plot(src, geom_4326, pad_m=PAD_M)
    except ValueError:
        return None
    shape = patch.image.shape[:2]
    if min(shape) < 8:
        return None
    geom_img = geom_to_imagery_crs(src, geom_4326)
    b_patch = ip.boundaries_on_patch(b_src, patch.transform, shape, patch.bounds)
    edge = ip.edge_response(patch.image, b_patch)
    ring = ip.rasterize_ring(geom_img, patch.transform, shape, width_px=2)
    if ring.sum() < 4:
        return None
    res_x = abs(patch.transform.a)
    search_px = max(3, int(round(SEARCH_M / res_x)))
    al = ip.align(edge, ring, search_px)
    return al, patch.transform


def correct_village(village, max_plots: int | None = None) -> gpd.GeoDataFrame:
    """Run the full method over a loaded `Village`; return a contract-shaped predictions GDF.

    `max_plots` (optional) limits how many plots are processed — handy for quick iteration.
    """
    to_lonlat = Transformer.from_crs('EPSG:3857', 'EPSG:4326', always_xy=True)
    plots = village.plots
    index = list(plots.index)
    if max_plots is not None:
        index = index[:max_plots]

    # ---- pass 1: align every plot, collect raw offsets in metres (EPSG:3857) ----
    raw = {}
    b_src = open_imagery(village.boundaries_path) if village.boundaries_path else None
    try:
        with open_imagery(village.imagery_path) as src:
            for pn in index:
                geom = plots.loc[pn, 'geometry']
                out = _align_plot(src, b_src, geom)
                if out is None:
                    continue
                al, tf = out
                dx = al.dcol * tf.a          # +cols -> +x (east)
                dy = al.drow * tf.e          # +rows -> -y (south); tf.e is negative
                raw[pn] = {'al': al, 'dx': dx, 'dy': dy}
    finally:
        if b_src is not None:
            b_src.close()

    # ---- learn a robust village drift prior from the confident alignments ----
    good = [(r['dx'], r['dy'], r['al'].sharpness, r['al'].support)
            for r in raw.values() if r['al'].sharpness > 4.0 and r['al'].support > 0.18]
    if good:
        arr = np.array(good)
        w = arr[:, 2] * arr[:, 3]
        prior_dx = _weighted_median(arr[:, 0], w)
        prior_dy = _weighted_median(arr[:, 1], w)
    else:
        prior_dx = prior_dy = 0.0

    # ---- pass 2: finalize geometry, confidence, decision (no image work) ----
    rows = []
    for pn in index:
        geom = plots.loc[pn, 'geometry']
        props = plots.loc[pn]
        ratio = conf.area_ratio(props.get('map_area_sqm'),
                                props.get('recorded_area_sqm'),
                                props.get('pot_kharaba_ha'))

        if pn not in raw:
            rows.append({'plot_number': pn, 'status': 'flagged', 'confidence': 0.1,
                         'method_note': 'no usable imagery under plot; kept official',
                         'geometry': geom})
            continue

        r = raw[pn]
        al = r['al']
        dx, dy = r['dx'], r['dy']
        offset_m = math.hypot(dx, dy)
        prior_dist = math.hypot(dx - prior_dx, dy - prior_dy)

        # weak/ambiguous alignment -> trust the village prior instead of a noisy per-plot peak
        used_prior = False
        if al.sharpness < 3.0 or al.support < 0.12:
            dx, dy = prior_dx, prior_dy
            offset_m = math.hypot(dx, dy)
            prior_dist = 0.0
            used_prior = True

        d = conf.assess(offset_m, al.sharpness, al.support, al.support_before, prior_dist, ratio)

        if d.snap_zero:
            new_geom = geom
        else:
            shifted_img = translate(geom_to_imagery_crs_cached(geom), xoff=dx, yoff=dy)
            new_geom = shp_transform(lambda xs, ys, z=None: to_lonlat.transform(xs, ys), shifted_img)

        note = d.note + (' [village-prior fallback]' if used_prior else '')
        out_geom = new_geom if d.status == 'corrected' else geom
        if not out_geom.is_valid:
            out_geom = make_valid(out_geom)
        rows.append({'plot_number': pn, 'status': d.status,
                     'confidence': round(float(d.confidence), 3),
                     'method_note': note,
                     'geometry': out_geom})

    gdf = gpd.GeoDataFrame(rows, geometry='geometry', crs='EPSG:4326')
    gdf['plot_number'] = gdf['plot_number'].astype(str)
    gdf.attrs['prior_dx'] = prior_dx
    gdf.attrs['prior_dy'] = prior_dy
    return gdf[['plot_number', 'status', 'confidence', 'method_note', 'geometry']]


# A tiny module-level cache so pass 2 can reproject a geometry to 3857 without reopening the raster.
_PROJ_4326_TO_3857 = Transformer.from_crs('EPSG:4326', 'EPSG:3857', always_xy=True)


def geom_to_imagery_crs_cached(geom_4326):
    """Reproject a lon/lat geometry to EPSG:3857 (web-mercator metres) for translation."""
    return shp_transform(lambda xs, ys, z=None: _PROJ_4326_TO_3857.transform(xs, ys), geom_4326)
