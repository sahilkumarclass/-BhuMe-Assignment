"""Low-level image work: edge-response maps, ring rasterization, cross-correlation alignment.

Everything here operates on a single plot's image *patch* (a small RGB crop around the plot,
plus the affine transform from the starter kit's `patch_for_plot`). We never touch lon/lat here —
shifts come out in pixels, and the pipeline converts them to metres/CRS.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.windows import from_bounds
from scipy.ndimage import gaussian_filter, sobel
from scipy.signal import fftconvolve
from shapely.geometry.base import BaseGeometry


def _norm(a: np.ndarray) -> np.ndarray:
    """Scale an array to [0, 1] (robust to flat input)."""
    a = a.astype(np.float32)
    lo, hi = float(a.min()), float(a.max())
    if hi - lo < 1e-9:
        return np.zeros_like(a)
    return (a - lo) / (hi - lo)


def imagery_edges(image: np.ndarray) -> np.ndarray:
    """Gradient-magnitude edge map from an RGB patch, normalized to [0, 1].

    Field bunds, tracks and tank edges show up as strong gradients; this is the signal we trust
    most where the boundary hints are thin (under canopy, near buildings).
    """
    gray = image.astype(np.float32).mean(axis=2)
    gray = gaussian_filter(gray, sigma=1.0)
    gx = sobel(gray, axis=1)
    gy = sobel(gray, axis=0)
    mag = np.hypot(gx, gy)
    return _norm(mag)


def boundaries_on_patch(b_src, patch_transform, patch_shape,
                        patch_bounds) -> np.ndarray | None:
    """Sample an already-open `boundaries.tif` dataset over the patch footprint, onto its grid.

    The hints raster is coarser than the imagery and single-band, so we resample it onto the same
    grid as the RGB patch. Returns [0, 1] floats, or None if the patch doesn't overlap the hints.
    (Pass an open rasterio dataset — opening the file per plot is far too slow over a whole village.)
    """
    if b_src is None:
        return None
    left, bottom, right, top = patch_bounds
    bl, bb, br, bt = b_src.bounds
    if right <= bl or left >= br or top <= bb or bottom >= bt:
        return None
    window = from_bounds(left, bottom, right, top, transform=b_src.transform)
    arr = b_src.read(
        1,
        window=window,
        out_shape=patch_shape,
        resampling=Resampling.bilinear,
        boundless=True,
        fill_value=0,
    )
    return _norm(arr)


def edge_response(image: np.ndarray, boundaries: np.ndarray | None,
                  hint_weight: float = 1.0) -> np.ndarray:
    """Combine the imagery gradient with the (optional) boundary hints into one edge-response map.

    The hints are a pre-baked learned edge detector — strong on open land, unreliable under cover.
    We take the per-pixel maximum so the hints *add* edges where they're confident without erasing
    the imagery signal where they're blank.
    """
    img_e = imagery_edges(image)
    if boundaries is None:
        return img_e
    return np.maximum(img_e, hint_weight * boundaries)


def rasterize_ring(geom_imgcrs: BaseGeometry, transform, shape,
                   width_px: int = 1) -> np.ndarray:
    """Burn a plot's *outline* (not its fill) onto the patch grid as a [0, 1] template.

    We correlate the outline (rather than a filled mask) because matching the specific perimeter
    shape against detected edges is far less ambiguous than matching area against brightness.
    """
    ring = geom_imgcrs.boundary
    out = rasterize(
        [(ring, 1)],
        out_shape=shape,
        transform=transform,
        fill=0,
        all_touched=True,
        dtype='uint8',
    ).astype(np.float32)
    if width_px > 1:
        out = (gaussian_filter(out, sigma=width_px / 2.0) > 0.05).astype(np.float32)
    return out


@dataclass
class Alignment:
    """Result of aligning one plot's outline to the edge response.

    `drow, dcol` is the pixel shift to apply to the outline (row down = south). `peak` is the
    correlation score at that shift; `sharpness` is the peak's contrast against the search window
    (a higher, lonelier peak = a more trustworthy fix); `support` is the mean edge response under
    the outline *after* shifting (how much real edge actually underlies the moved perimeter).
    """

    drow: float
    dcol: float
    peak: float
    sharpness: float
    support: float
    support_before: float


def _subpixel(corr: np.ndarray, r: int, c: int) -> tuple[float, float]:
    """Parabolic sub-pixel refinement of an integer correlation peak at (r, c)."""
    dr = dc = 0.0
    if 0 < r < corr.shape[0] - 1:
        a, b, cc = corr[r - 1, c], corr[r, c], corr[r + 1, c]
        denom = a - 2 * b + cc
        if abs(denom) > 1e-9:
            dr = 0.5 * (a - cc) / denom
    if 0 < c < corr.shape[1] - 1:
        a, b, cc = corr[r, c - 1], corr[r, c], corr[r, c + 1]
        denom = a - 2 * b + cc
        if abs(denom) > 1e-9:
            dc = 0.5 * (a - cc) / denom
    return float(np.clip(dr, -1, 1)), float(np.clip(dc, -1, 1))


def align(edge_resp: np.ndarray, ring: np.ndarray,
          search_px: int) -> Alignment:
    """Find the pixel shift that best lands the outline `ring` on the edge response.

    Cross-correlation (FFT) of the edge response with the outline template; the peak within
    `search_px` of zero is the estimated drift. We also report how confident that peak is and how
    much edge actually underlies the perimeter before vs after the move.
    """
    # full cross-correlation; centre of `corr` corresponds to zero shift
    corr = fftconvolve(edge_resp, ring[::-1, ::-1], mode='same')
    h, w = corr.shape
    cr, cc = h // 2, w // 2

    # restrict the peak search to a window around zero shift (drift is bounded)
    rmask = np.zeros_like(corr, dtype=bool)
    r0, r1 = max(0, cr - search_px), min(h, cr + search_px + 1)
    c0, c1 = max(0, cc - search_px), min(w, cc + search_px + 1)
    rmask[r0:r1, c0:c1] = True
    windowed = np.where(rmask, corr, -np.inf)

    pr, pc = np.unravel_index(int(np.argmax(windowed)), corr.shape)
    peak = float(corr[pr, pc])

    # sharpness: how far the peak stands above the rest of the search window
    win = corr[r0:r1, c0:c1]
    med = float(np.median(win))
    mad = float(np.median(np.abs(win - med))) + 1e-6
    sharpness = (peak - med) / (1.4826 * mad)

    sdr, sdc = _subpixel(corr, pr, pc)
    drow = (pr - cr) + sdr
    dcol = (pc - cc) + sdc

    ring_n = ring.sum() + 1e-6
    support_before = float((edge_resp * ring).sum() / ring_n)
    shifted = _shift_mask(ring, int(round(drow)), int(round(dcol)))
    support = float((edge_resp * shifted).sum() / (shifted.sum() + 1e-6))

    return Alignment(drow=drow, dcol=dcol, peak=peak, sharpness=float(sharpness),
                     support=support, support_before=support_before)


def _shift_mask(mask: np.ndarray, drow: int, dcol: int) -> np.ndarray:
    """Integer-shift a mask, zero-filling the exposed border."""
    out = np.zeros_like(mask)
    h, w = mask.shape
    sr0, sr1 = max(0, drow), min(h, h + drow)
    dr0, dr1 = max(0, -drow), min(h, h - drow)
    sc0, sc1 = max(0, dcol), min(w, w + dcol)
    dc0, dc1 = max(0, -dcol), min(w, w - dcol)
    if sr1 > sr0 and sc1 > sc0:
        out[sr0:sr1, sc0:sc1] = mask[dr0:dr1, dc0:dc1]
    return out
