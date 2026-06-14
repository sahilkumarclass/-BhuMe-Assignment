# BhuMe Take-Home — Plain-English Guide + Our Solution

This is a walkthrough of the BhuMe land-boundary take-home: **what the task actually is, why the
problem exists, how it's scored, and the method we built to solve it** (in `solver/`, run by
`solve.py`). If you read nothing else, read the TL;DR and "Our approach".

> The official starter-kit docs are in `README.md` (the helpers) and `CONTRACT.md` (the exact
> input/output spec). This file is the *human* explanation and our solution write-up.

---

## TL;DR

- **The problem:** Maharashtra's official land-plot outlines are the right *shape* but sit several
  metres off the *real field* (an artefact of how old paper maps were fitted onto satellite imagery).
- **The task:** for each plot, either **`corrected`** — return a better boundary + a **confidence**
  (0–1) — or **`flagged`** — say "I looked, I'm not sure" and keep the official outline. Plots you
  skip cost nothing.
- **What's graded most:** not just *being right*, but **knowing when you're right** (confidence
  calibration). They grade the *method*, not hand-edited shapes.
- **Our method:** for each plot, slide its outline over the satellite image to find the field edges
  it belongs on (cross-correlation), back that up with a village-wide drift estimate, and turn the
  quality of each match into an honest confidence. Flag plots whose *recorded area* disagrees with
  their drawn shape, or where there's no clear edge to lock onto.

---

## Part 1 — Understand the problem

### What a "plot" is
A **plot** is one drawn outline on the cadastral (land-records) map — the thing you correct. Each
has a unique `plot_number`. Alongside the map, each plot has a written revenue record, the **7/12
extract**, which lists *who holds the land* and *how much area is on file* — but **not the shape**.
The shape comes from the old **cadastral/village map**. So every plot carries two things that don't
always agree: a **recorded area** (from the 7/12) and a **drawn outline** (from the map).

A few terms you'll see in the data (`input.geojson`):
- **survey / gat number** (e.g. `142`) — a parcel in the record; can be subdivided into a **hissa**
  (`142/2`). One plot can bundle several surveys.
- **holding** — one recorded party's share within a survey, each with its own area. A plot's
  `recorded_area` is the **sum of all holdings**.
- **pot-kharaba** — *uncultivable* land inside the plot, recorded **separately**. So the full extent
  the outline should enclose ≈ `recorded_area` **+** `pot_kharaba`. (This matters: compare your
  geometry against the **total**, not the cultivable figure alone.)

### Why the outlines are wrong
The maps were drawn by hand (chain / plane-table survey) at ~1:4,000–1:10,000, then **scanned and
georeferenced** onto satellite imagery by fitting them to features visible in both (bunds, roads,
streams). That fit is only as good as those control points — where they're sparse, a plot **drifts**
off the field it describes.

### Two kinds of "wrong" (telling them apart is most of the job)
1. **Placement** — the shape is roughly right but **off the real field**. The *fixable* case: you
   can see where it should go, so you nudge it. → `corrected`.
2. **Area** — the **drawn shape itself disagrees** with the recorded area (too big / too small /
   wrong outline). Moving it won't help. → usually `flagged`.

The clue is the ratio **`drawn area ÷ recorded total`**: near **1.0** → likely a placement problem
(fixable); far from 1.0 → an area/record problem (don't just move it).

---

## Part 2 — The data you get (per village)

| File | What it is | How to treat it |
|---|---|---|
| `input.geojson` | Every official plot: `plot_number`, `map_area_sqm` (drawn area), `recorded_area_sqm`, `pot_kharaba_ha`, `surveys`. | **Start here** — this is what you transform. |
| `imagery.tif` | Satellite raster of the village (RGB). | **Primary signal** — the real field is only visible here. |
| `boundaries.tif` | Auto-detected field edges (a pre-baked edge detector). | **Rough hint** — strong on open land, unreliable under canopy/buildings. A nudge, never an answer. |
| `example_truths.geojson` | A handful of hand-aligned "true" boundaries. | **Self-scoring only** — too few to tune on. |

You get the **whole village at once**, so you can use neighbouring context.

> ⚠️ **Setup note for this repo:** the village files now live in
> `data/34855_vadnerbhairav_chandavad_nashik/`. You still need to **download
> `example_truths.geojson`** from the site's *Get started* page into that folder to self-score —
> the solution runs without it, but `score()` needs it.

---

## Part 3 — What you must return (the contract)

Write `predictions.geojson` (a GeoJSON `FeatureCollection`, EPSG:4326 lon/lat) with one feature per
plot you make a claim about:

| Field | Meaning |
|---|---|
| `plot_number` | Echo the plot id exactly. |
| `status` | `corrected` (you moved it) or `flagged` (looked, not sure → keep official). |
| `confidence` | 0–1, for corrected plots. **This is scored — make it mean something.** |
| `method_note` | Optional: how you got it / why you flagged it. |
| `geometry` | Your predicted boundary (corrected) or the original (flagged). |

Omitted plots = "not attempted" (no penalty, no credit). You are **not** expected to fix every plot.

---

## Part 4 — How it's scored (and the tiers)

Against a **hidden** set of hand-checked truths:
- **A. Accuracy** — how close your corrected boundaries land, by **IoU** (overlap, 0–1) and centroid
  distance, always **vs the official starting position** (so what counts is the *improvement* you
  add). IoU ≥ 0.5 = a solid hit.
- **B. Confidence calibration** ★ **weighed most** — does your confidence *rank* good fixes above bad
  ones? Scored by **AUC** (0.5 = chance, 1 = perfect). Flat/random confidence sits at 0.5.
- **C. Restraint** — flagging honestly protects you; and **moving a plot that was already correct
  counts against you**.

**Tiers:** Bronze *(it runs, honest correct/flag, valid output)* → Silver *(corrected plots
measurably beat the official position)* → Gold *(confidence tracks accuracy)* → Platinum *(one
method generalises across villages without hand-tuning)*.

The metrics only **rank**; the **method review** (your code + 5-min video + AI transcripts) decides.
A clear, thin solution you can explain beats a higher-scoring one you can't.

---

## Part 5 — Our approach (the method in `solver/`)

The core idea: **the drift is mostly a local translation**, and the real field edges are visible in
the imagery. So for each plot we find the shift `(dx, dy)` that best lands its outline on those
edges, then judge how much to trust it.

### Step 1 — Build an "edge-response" map under each plot  (`solver/imageproc.py`)
For a small image patch around the plot we make a map of *where edges are*:
- a **gradient/Sobel** edge map from the satellite RGB (bunds, tracks, tank edges show as strong
  gradients) — the signal we trust where hints are thin;
- combined (per-pixel max) with the resampled **`boundaries.tif`** hints — a learned edge detector
  that *adds* edges where it's confident without erasing the imagery signal.

### Step 2 — Align the outline to the edges  (`align()` in `imageproc.py`)
We rasterise the plot's **outline** (not its fill — the perimeter shape is far less ambiguous to
match) and **cross-correlate** it (via FFT) against the edge-response, searching ±40 m. The peak,
refined to sub-pixel, is the estimated drift `(dx, dy)`. We also record:
- **sharpness** — how far the peak stands above the rest of the window (a lonely, tall peak = a
  trustworthy match);
- **edge support** — how much real edge actually underlies the outline *after* the move.

*(A synthetic test in development confirmed the aligner recovers a known shift exactly, signs
included.)*

### Step 3 — Learn a village-wide drift prior  (`solver/pipeline.py`)
We take the **confident** per-plot offsets and compute a **robust (weighted-median) village drift**.
This is auto-estimated per village — no hand-tuning — which is what makes the method *generalise*
(the Platinum idea). Plots with a weak/ambiguous peak **fall back to this prior** instead of trusting
a noisy local match.

### Step 4 — Confidence that means something  (`solver/confidence.py`)
Calibration is graded hardest, so confidence is built **only from truth-free evidence** that should
predict accuracy:
- peak **sharpness**, edge **support**, and **support gain** (did moving actually help?);
- **area agreement** — `drawn ÷ (recorded + pot-kharaba)` near 1.0 → trust;
- **prior agreement** — offset close to the village drift → trust; wild outlier → distrust.

These blend into a single 0–1 confidence.

### Step 5 — Decide: correct, flag, or leave alone  (`assess()` in `confidence.py`)
- **Flag** when the **area ratio is extreme** (shape disagrees with the record — moving won't help),
  or there's **no trustworthy edge** to align to (low sharpness *and* low support), or the record is
  empty.
- **Restraint** — if the estimated drift is tiny (< ~2.5 m) and the outline already sits on edges, we
  **keep the official position** so already-correct plots aren't moved.
- Otherwise **correct**: translate the official outline by `(dx, dy)` and return it.

### Files
```
solve.py                 # CLI: load → correct → write predictions.geojson → self-score (if truths)
viz.py                   # save before/after overlays (red=official, green=predicted) for spot-checks
solver/imageproc.py      # edge response, outline rasterization, FFT cross-correlation alignment
solver/pipeline.py       # two-pass orchestration + village drift prior
solver/confidence.py     # confidence blend + correct/flag/keep decision
bhume/                   # starter-kit helpers (unchanged): load, patch_for_plot, score, write_predictions
```

---

## Part 6 — How to run

```bash
uv sync                                                   # one-time: install deps into .venv
uv run solve.py data/34855_vadnerbhairav_chandavad_nashik # → writes predictions.geojson (+ scorecard if truths present)
uv run viz.py  data/34855_vadnerbhairav_chandavad_nashik  # → out/overlays/*.png to eyeball quality
```
Full village (~2,457 plots) runs in well under a minute. Pass a number as a 2nd arg to
`solve.py` to limit plots for quick iteration, e.g. `uv run solve.py data/<village> 100`.

---

## Part 7 — Where it stands & how to self-score

- **Runs end-to-end** on Vadnerbhairav: ~2,200 corrected, ~250 flagged, confidence spread 0.32–0.98
  (not flat — so it can actually rank). ✅ Bronze.
- **Spot-checks look right:** in the overlays, high-confidence corrections snap onto the visible
  field; flagged plots sit where the edge is genuinely ambiguous. (See `out/overlays/`.)
- **To prove Silver/Gold numerically:** download `example_truths.geojson` and re-run `solve.py` — it
  prints the same IoU / improvement / AUC metrics the graders use. The 6-truth sample is only
  *directional*; **don't tune to it** (the real grade is a hidden set).

---

## Part 8 — What's left for you (and ideas to push further)

**Hand-in checklist (from the spec):** one GitHub repo with the code + `predictions.geojson` per
village + a `/transcripts` folder (your AI chats), plus a **5-minute video** and a short Google Form.
No written report — the video + transcripts carry the explanation.

**For the video:** walk the *judgment*, not the plumbing — why edge-correlation, what your confidence
*means*, why you flag area-mismatches, and show a couple of `out/overlays/` before/afters.

**Ideas to go further (optional):**
- Per-edge snapping (not just a whole-plot translation) for plots that are sheared, not just shifted.
- A smooth spatial field of offsets (local-neighbour median) for non-uniform drift.
- Calibrate the confidence→accuracy mapping against the truths *once*, lightly, without overfitting.
- Run the **second village (Malatavadi)** unchanged to demonstrate generalisation (Platinum).
