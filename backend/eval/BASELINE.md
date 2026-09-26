# Relevance baseline

The relevance gate (`tests/test_relevance_gate.py`) fails if nDCG@10 on the query set
below drops more than the tolerance under the metric recorded here. **Editing this file
is the only way to move the floor**, so a change to it should be reviewed like code.

## Status: PROVISIONAL

These numbers were measured on `v1_baseline_subset.json`, whose 29 labels were made by
reading the `off` config's own results. That biases them toward this config and toward
the current ranker generally. They are committed so the gate exists and catches
regressions now; they are to be **replaced** by numbers from the pooled, blind-labeled
set (`v2_pooled`, built with `python -m backend.eval.pool`) once that labeling is done.

## Config that produced these numbers

The live default: `SEARCH_MODE=vector`, `RERANK=off` (no hybrid, no structured rerank),
with the gender-fallback and `category_audience` fixes applied.

Measured 2026-09-23 (`backend/eval/runs/vector.json`).

| Metric | Value |
|---|---|
| nDCG@10 | 0.8599 |
| Recall@20 | 0.8794 |
| Precision@10 | 0.6621 |
| MRR | 0.9080 |
| Zero-result rate | 0.0000 |

## Latency

No latency ceiling is enforced yet. Search currently takes ~4s p50 / ~7.9s p95 because of
the full-table "exact-type augmentation" fetch (see the latency profile). The ceiling is
turned on by setting `latency_ceiling_ms` below once that fix lands. It must be calibrated
on the machine that runs the gate (CI), because the round trip to Supabase dominates.

## Reference output snapshots moved when query encoding switched from torch to ONNX

Since the API encodes text queries with the ONNX text encoder (`backend/ml/`), embeddings differ from the
torch-era ones by float noise (max element difference 3.6e-7; minimum cosine to torch 0.999999999999 over 29 eval
queries; 0 of 129 texts differ in token ids). Vector-mode score fields therefore differ in the 7th decimal (largest
`similarity` delta 1.6e-7, `final_score` 3.6e-8), so byte hashes of the full result dumps changed. **No result set or
order changed in any of the 29 queries**, and the nearest non-tied pair of adjacent scores (3.5e-6) is ~100x the largest
noise, so rank metrics (nDCG@10, recall, MRR) are unaffected. Do not diff against torch-era hashes any more.

| 29-query dump (top-20, `sort_keys`) | torch-era sha256 (first 16) | ONNX-era reference (first 16) |
|---|---|---|
| vector | `83c927e64e156520` | `b32788b8b0b9a0fa` |
| hybrid 2:1 | `3d161b2cfc2b7874` | `3d161b2cfc2b7874` (hybrid scores are rank-derived, so byte-identical) |

The dump files themselves live outside the repo (they are regenerated from the live catalog; they change if the
catalog changes, not only if search does).

## Ranking is now deterministic under floating-point noise (tie-break added)

`apply_final_ranking` and `rerank_candidates` now sort with `deterministic_rank_key` (backend/app.py):
candidates whose `final_score` differs by less than `TIE_BREAK_EPSILON` (1e-5) are treated as tied and
ordered by `TIE_BREAK_KEY` (product id) instead of by score, so ranking cannot be decided at the scale of
ONNX-vs-torch floating-point noise (measured up to 1.16e-6 on final_score). Reference snapshot hashes moved
again as a result -- do not diff against the pre-tie-break ("ONNX-era reference") hashes either.

**Important, not a bug:** re-running the 29-query text set showed **10 of 29 queries with result SET
changes**, not just reordering. Root cause, verified directly: the "exact-type augmentation" path
(`exact_type_products`) supplies candidate rows with no similarity score at all (`similarity=0.0` for
every augmented row), so on many queries a large group of augmented candidates are *exactly* tied on
`final_score` -- e.g. "black dress" had 9 candidates tied at exactly `0.5200000000`, all with
`similarity=0.0`. Before this change, Python's stable sort left such ties in whatever order the
augmentation RPC happened to return them (`exact_type_products` orders by `ctid`, i.e. physical row
storage order) -- so which subset of an over-full tied group landed inside the top-20 window was already
arbitrary, just arbitrary-and-silent rather than arbitrary-and-documented. The tie-break makes this
explicit and now decides those cases by product id, which is portable and reproducible; the previous
`ctid`-based ordering was not guaranteed stable across a table rewrite (VACUUM FULL, migration, restore).
This is not something to "fix" by changing the tie-break; it reflects a real gap in the augmentation
path (those candidates carry no relevance signal to rank by at all), out of scope here.

Overall on the 29-query set: 26 of 29 queries have at least one `similarity=0.0` augmented row in their
top-20, and 12 of those have the position-20 cutoff itself sitting inside a tied group. Image search never
hits this path (the augmentation only runs for `search_mode == "text"`), so its 100-image comparison shows
0 set and 0 order changes.

**Margin, measured correctly this time:** "nearest non-tied gap" must mean the smallest gap between two
candidates in *different* tie-break buckets (same-bucket order is already immune to noise, decided by id).
Text: smallest cross-bucket gap 7.465e-6 vs. max observed noise 3.6e-8 (~207x). Image: 4.496e-6 vs. 1.16e-6
(~3.9x, up from ~1.1x on the pre-tie-break raw-adjacent-gap metric). Caveat: rounding-based bucketing is
not immune to a pair straddling a bucket boundary in the worst case (a boundary-straddling pair's gap could
still be arbitrarily small); the margins above are what was empirically observed on this catalog, not a
theoretical guarantee.

| 29-query dump (top-20, `sort_keys`) | ONNX-era reference sha256 (first 16) | tie-break-era reference (first 16) |
|---|---|---|
| vector | `b32788b8b0b9a0fa` | `3b382b2189fa586c` |
| hybrid 2:1 | `3d161b2cfc2b7874` | `e73a0e28e7021eab` |

## Machine-readable baseline

```json
{
  "status": "provisional",
  "queryset": "backend/eval/querysets/v1_baseline_subset.json",
  "config": {"retrieval_mode": "vector", "rrf_weights": null, "rerank_mode": "off"},
  "metrics": {
    "mean_ndcg_at_10": 0.8599,
    "mean_recall_at_20": 0.8794,
    "mean_precision_at_10": 0.6621,
    "mrr": 0.9080,
    "zero_result_rate": 0.0
  },
  "ndcg_tolerance": 0.02,
  "latency_ceiling_ms": {"p50": null, "p95": null}
}
```
