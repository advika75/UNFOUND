"""CLI for offline zero-shot and frozen-CLIP linear-probe experiments."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.ml.experiments import evaluate_zero_shot, train_linear_probe


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline GemMode ML experiments"); sub = parser.add_subparsers(dest="command", required=True)
    zero = sub.add_parser("zero-shot"); zero.add_argument("--dataset", type=Path, required=True); zero.add_argument("--output", type=Path, required=True)
    probe = sub.add_parser("linear-probe"); probe.add_argument("--dataset", type=Path, required=True); probe.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(); rows = load_jsonl(args.dataset)
    usable = [row for row in rows if row.get("embedding") and row.get("product_type") and row.get("split")]
    if not usable: raise SystemExit("No labeled records with embeddings and split fields. Export with --include-embeddings after stable-image repair.")
    if args.command == "zero-shot":
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("clip-ViT-B-32"); labels = sorted({row["product_type"] for row in usable}); prompts = [f"a product photo of {label}" for label in labels]
        vectors = model.encode(prompts, normalize_embeddings=True).tolist(); metrics = evaluate_zero_shot([row["embedding"] for row in usable], [row["product_type"] for row in usable], dict(zip(labels, vectors)))
    else:
        train = [row for row in usable if row["split"] == "train"]; test = [row for row in usable if row["split"] == "test"]
        if len({row["product_type"] for row in train}) < 2 or not test: raise SystemExit("Insufficient grouped train/test class coverage for a credible linear probe.")
        _, metrics = train_linear_probe([row["embedding"] for row in train], [row["product_type"] for row in train], [row["embedding"] for row in test], [row["product_type"] for row in test])
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(metrics, indent=2), encoding="utf-8"); print(json.dumps(metrics, indent=2))


if __name__ == "__main__": main()
