from pathlib import Path

import pytest

from backend.ml.experiments import (
    classification_metrics, dataset_hash, evaluate_zero_shot, save_versioned_model,
    style_label_readiness, train_linear_probe,
)
from backend.training_data import (
    grouped_split, hard_negative_kind, readiness_report, relevance_metrics,
    trusted_training_record, write_records,
)
from backend.search_failure_log import append_search_evaluation


BASE_URL = "https://project.supabase.co"


def product(**extra):
    row = {"id":"p1", "brand_id":"b1", "product_name":"Black crop top", "description":"Fitted party crop top", "image_url":f"{BASE_URL}/storage/v1/object/public/product-images/b1/p1-1.jpg", "image_status":"VALID", "classifier_confidence":.94, "classification_source":"manual", "normalized_main_category":"women", "normalized_subcategory":"women-tops", "audience":"Women", "metadata":{"colour":"Black", "style":["Party"]}, "embedding":[1 / (512 ** .5)] * 512}
    row.update(extra); return row


def test_trusted_label_export_and_low_confidence_exclusion(tmp_path):
    record = trusted_training_record(product(), supabase_url=BASE_URL)
    assert record["product_type"] == "Crop Top" and record["label_confidence"] == .94
    assert trusted_training_record(product(classifier_confidence=.79), supabase_url=BASE_URL) is None
    assert trusted_training_record(product(image_url="https://instagram.example/x.jpg"), supabase_url=BASE_URL) is None
    output = tmp_path / "labels.jsonl"; write_records([record], output, "jsonl"); assert '"product_type": "Crop Top"' in output.read_text()


def test_deterministic_brand_split_has_no_brand_leakage():
    rows = [{"product_id":f"p{i}", "brand_id":f"b{i//3}"} for i in range(30)]
    first = grouped_split(rows, seed="fixed"); second = grouped_split(rows, seed="fixed")
    assert first == second
    memberships = {}
    for split, values in first.items():
        for row in values: memberships.setdefault(row["brand_id"], set()).add(split)
    assert all(len(splits) == 1 for splits in memberships.values())


def test_relevance_metrics_and_hard_negative_construction():
    rows = [{"query":"black crop top", "rank":1, "label":3}, {"query":"black crop top", "rank":2, "label":0}, {"query":"black crop top", "rank":3, "label":2}]
    metrics = relevance_metrics(rows)
    assert metrics["average"]["mrr"] == 1 and metrics["queries"]["black crop top"]["precision@5"] == pytest.approx(2/3)
    assert hard_negative_kind("black crop top", {"product_name":"Black mini dress"}) == "same_colour_wrong_family"
    assert hard_negative_kind("black crop top", {"product_name":"White crop top"}) == "right_type_wrong_colour"


def test_zero_shot_evaluation_and_classification_metrics():
    result = evaluate_zero_shot([[1,0], [0,1]], ["tops", "bags"], {"tops":[1,0], "bags":[0,1]})
    assert result["accuracy"] == 1 and result["macro_f1"] == 1
    assert classification_metrics(["a","b"], ["a","a"])["confusion_matrix"]["b"]["a"] == 1


def test_linear_probe_and_versioned_metadata(tmp_path):
    model, metrics = train_linear_probe([[2,0],[1,0],[0,2],[0,1]], ["top","top","bag","bag"], [[3,0],[0,3]], ["top","bag"])
    assert metrics["accuracy"] == 1
    records = [{"product_id":"1"}, {"product_id":"2"}]
    saved = save_versioned_model(model, output_dir=tmp_path, model_name="product_type_linear_v1", dataset_records=records, feature_schema=["clip_0","clip_1"], metrics=metrics, hyperparameters={"class_weight":"balanced"})
    assert Path(saved["model_path"]).name != "model.pkl" and saved["dataset_hash"] == dataset_hash(records)
    assert Path(saved["metadata_path"]).exists() and saved["deployment_status"] == "OFFLINE_EVALUATION_ONLY"


def test_training_readiness_gate_and_style_minimums():
    small = [{"product_type":"Crop Top", "subcategory":"tops", "style":["Party"]}] * 10
    assert readiness_report(small)["decision"] == "TRAINING_NOT_READY"
    assert style_label_readiness(small, minimum_per_style=11)["train"] is False


def test_anonymous_search_failure_log_contains_reviewable_signals(tmp_path):
    path = tmp_path / "failures.jsonl"
    append_search_evaluation(path, query="black crop top", response={"query_understanding":{"product_type":"crop-top"}, "results":[{"id":"p1", "product_name":"Black crop top", "final_score":.9, "score_breakdown":{"product_type_match":1}}]}, review="PARTIAL")
    row = __import__("json").loads(path.read_text())
    assert row["review"] == "PARTIAL" and row["candidate_product_types"] == ["crop-top"]
    assert "user_id" not in row
