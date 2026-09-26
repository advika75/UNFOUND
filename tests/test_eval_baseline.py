import pytest

from backend.eval.baseline import BaselineError, latency_ceilings, load_baseline, ndcg_floor, parse_baseline

DOC = """# x

## Machine-readable baseline

```json
{"queryset": "q.json", "config": {"retrieval_mode": "vector", "rrf_weights": null, "rerank_mode": "off"},
 "metrics": {"mean_ndcg_at_10": 0.80}, "ndcg_tolerance": 0.02, "latency_ceiling_ms": {"p50": 300, "p95": null}}
```
"""


def test_floor_is_baseline_minus_tolerance():
    assert ndcg_floor(parse_baseline(DOC)) == pytest.approx(0.78)


def test_latency_ceilings_allow_partial_and_null():
    assert latency_ceilings(parse_baseline(DOC)) == {"p50": 300, "p95": None}


def test_missing_latency_block_means_no_ceiling():
    doc = DOC.replace(', "latency_ceiling_ms": {"p50": 300, "p95": null}', "")
    assert latency_ceilings(parse_baseline(doc)) == {"p50": None, "p95": None}


def test_missing_block_is_an_error():
    with pytest.raises(BaselineError, match="no '## Machine-readable baseline'"):
        parse_baseline("# nothing here")


def test_missing_required_keys_is_an_error():
    with pytest.raises(BaselineError, match="missing keys"):
        parse_baseline(DOC.replace('"ndcg_tolerance": 0.02, ', ""))


def test_invalid_json_is_an_error():
    with pytest.raises(BaselineError, match="invalid"):
        parse_baseline("## Machine-readable baseline\n\n```json\n{nope}\n```")


def test_committed_baseline_file_parses_and_has_a_sane_floor():
    baseline = load_baseline()
    assert 0.0 < ndcg_floor(baseline) < baseline["metrics"]["mean_ndcg_at_10"]
    assert baseline["config"]["rerank_mode"] in ("off", "structured")
