"""
solver — an image-based method that corrects shifted cadastral plot boundaries.

The official outlines in `input.geojson` are the right *shape* but sit metres off the real
field. For each plot we estimate the local translation that snaps its outline onto the field
edges visible in `imagery.tif` (helped by the rough `boundaries.tif` hints), score how much we
trust that fix, and decide whether to return it (`corrected`) or keep the official one (`flagged`).

Public entry point: `solver.correct_village(village) -> GeoDataFrame` in the contract shape.
"""

from solver.pipeline import correct_village

__all__ = ['correct_village']
