"""Offline baselines and lightweight probes over frozen CLIP embeddings."""

from __future__ import annotations

import hashlib
import json
import pickle
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


def classification_metrics(y_true: list[str], y_pred: list[str]) -> dict[str, Any]:
    labels = sorted(set(y_true) | set(y_pred)); confusion = {actual: {predicted: 0 for predicted in labels} for actual in labels}
    for actual, predicted in zip(y_true, y_pred): confusion[actual][predicted] += 1
    per_class = {}
    for label in labels:
        tp = confusion[label][label]; fp = sum(confusion[other][label] for other in labels if other != label); fn = sum(confusion[label][other] for other in labels if other != label)
        precision = tp / (tp + fp) if tp + fp else 0; recall = tp / (tp + fn) if tp + fn else 0
        per_class[label] = {"precision": precision, "recall": recall, "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0, "support": sum(confusion[label].values())}
    return {"accuracy": sum(a == b for a, b in zip(y_true, y_pred)) / len(y_true) if y_true else 0, "macro_f1": sum(row["f1"] for row in per_class.values()) / len(per_class) if per_class else 0, "per_class": per_class, "confusion_matrix": confusion}


def zero_shot_predictions(product_embeddings: list[list[float]], prototype_embeddings: dict[str, list[float]]) -> list[str]:
    if not prototype_embeddings: raise ValueError("At least one text prototype is required")
    labels = list(prototype_embeddings); prototypes = np.asarray([prototype_embeddings[label] for label in labels], dtype=float); products = np.asarray(product_embeddings, dtype=float)
    prototypes /= np.maximum(np.linalg.norm(prototypes, axis=1, keepdims=True), 1e-12); products /= np.maximum(np.linalg.norm(products, axis=1, keepdims=True), 1e-12)
    return [labels[index] for index in np.argmax(products @ prototypes.T, axis=1)]


def evaluate_zero_shot(product_embeddings: list[list[float]], labels: list[str], prototype_embeddings: dict[str, list[float]]) -> dict[str, Any]:
    return classification_metrics(labels, zero_shot_predictions(product_embeddings, prototype_embeddings))


def train_linear_probe(train_x: list[list[float]], train_y: list[str], test_x: list[list[float]], test_y: list[str], *, random_state: int = 42) -> tuple[Any, dict[str, Any]]:
    from sklearn.linear_model import LogisticRegression
    if len(set(train_y)) < 2: raise ValueError("Linear probe requires at least two classes")
    model = LogisticRegression(max_iter=1000, class_weight="balanced", random_state=random_state); model.fit(np.asarray(train_x), train_y)
    return model, classification_metrics(test_y, model.predict(np.asarray(test_x)).tolist())


def dataset_hash(records: list[dict[str, Any]]) -> str:
    canonical = json.dumps(records, sort_keys=True, default=str, separators=(",", ":")); return hashlib.sha256(canonical.encode()).hexdigest()


def save_versioned_model(model: Any, *, output_dir: Path, model_name: str, dataset_records: list[dict[str, Any]], feature_schema: list[str], metrics: dict[str, Any], hyperparameters: dict[str, Any]) -> dict[str, Any]:
    version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"); artifact_name = f"{model_name}_{version}"
    output_dir.mkdir(parents=True, exist_ok=True); model_path = output_dir / f"{artifact_name}.pkl"; metadata_path = output_dir / f"{artifact_name}.json"
    model_path.write_bytes(pickle.dumps(model)); metadata = {"model": artifact_name, "training_date": datetime.now(timezone.utc).isoformat(), "dataset_hash": dataset_hash(dataset_records), "feature_schema": feature_schema, "metrics": metrics, "hyperparameters": hyperparameters, "deployment_status": "OFFLINE_EVALUATION_ONLY"}
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8"); return {**metadata, "model_path": str(model_path), "metadata_path": str(metadata_path)}


def style_label_readiness(records: list[dict[str, Any]], minimum_per_style: int = 50) -> dict[str, Any]:
    counts = Counter(style for row in records for style in (row.get("style") or [])); eligible = sorted(style for style, count in counts.items() if count >= minimum_per_style)
    return {"counts": dict(counts), "eligible_styles": eligible, "train": len(eligible) >= 3}
