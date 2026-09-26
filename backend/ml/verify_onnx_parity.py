"""Correctness gate: ONNX text embeddings must match the torch/SentenceTransformer path.

    python -m backend.ml.verify_onnx_parity --queries-file <json list of query strings> [--catalog-sample 100]

Compares, for every text, (a) the token ids the two paths feed the model and (b) the cosine similarity of
the final L2-normalized embeddings. Reports the MINIMUM similarity (not the mean) and exits non-zero if it is
below --min-cosine (default 0.9999). Needs torch + sentence-transformers (verification only, never the API).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path


def cosine(a, b) -> float:
    import numpy as np

    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def catalog_names(sample: int, seed: int) -> list[str]:
    from dotenv import load_dotenv

    load_dotenv("backend/.env", override=True)
    from backend.category_quality import fetch_all
    from backend.supabase_compat import create_supabase_client

    client = create_supabase_client(os.environ["SUPABASE_URL"], os.getenv("SUPABASE_ANON_KEY") or os.environ["SUPABASE_KEY"])
    names = sorted({str(row["product_name"]) for row in fetch_all(client, "products") if row.get("product_name")})
    return random.Random(seed).sample(names, min(sample, len(names)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--queries-file", type=Path, required=True)
    parser.add_argument("--catalog-sample", type=int, default=100)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--artifact-dir", default="backend/ml/artifacts/clip-vit-b-32-text")
    parser.add_argument("--min-cosine", type=float, default=0.9999)
    args = parser.parse_args()

    from sentence_transformers import SentenceTransformer

    from backend.ml.onnx_text_encoder import OnnxTextEncoder

    torch_model = SentenceTransformer(os.getenv("CLIP_MODEL_NAME", "clip-ViT-B-32"))
    onnx_encoder = OnnxTextEncoder(args.artifact_dir)

    queries = json.loads(args.queries_file.read_text(encoding="utf-8"))
    texts = [("query", q) for q in queries] + [("catalog", n) for n in catalog_names(args.catalog_sample, args.seed)]
    rows, token_mismatches = [], []
    for kind, text in texts:
        reference = torch_model.encode(text, normalize_embeddings=True)
        candidate = onnx_encoder.encode(text)
        rows.append((cosine(reference, candidate), kind, text))
        torch_ids = torch_model.tokenize([text])["input_ids"][0].tolist()
        onnx_ids = onnx_encoder._tokenizer.encode(text).ids
        if torch_ids != onnx_ids:
            token_mismatches.append((kind, text[:60], len(torch_ids), len(onnx_ids)))

    rows.sort()
    for kind in ("query", "catalog"):
        subset = [r[0] for r in rows if r[1] == kind]
        print(f"{kind:8} n={len(subset):3d}  min={min(subset):.9f}  mean={sum(subset)/len(subset):.9f}")
    print(f"OVERALL  n={len(rows):3d}  MIN cosine = {rows[0][0]:.9f}   (gate: >= {args.min_cosine})")
    print("lowest 3:", [(f"{c:.9f}", k, t[:40]) for c, k, t in rows[:3]])
    print(f"token-id mismatches vs the torch path's tokenization: {len(token_mismatches)} of {len(texts)}", token_mismatches[:5])
    return 0 if rows[0][0] >= args.min_cosine else 1


if __name__ == "__main__":
    sys.exit(main())
