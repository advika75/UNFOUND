"""Correctness gate: ONNX image embeddings must match the torch/SentenceTransformer path on REAL catalog images.

    python -m backend.ml.verify_onnx_image_parity [--count 100] [--seed 7]

Downloads real product images from the catalog, picks a diverse set (extreme aspect ratios and every non-RGB
mode present), and for each compares against the exact path the API uses today
(Image.open(bytes).convert("RGB") -> SentenceTransformer.encode(image, normalize_embeddings=True)):
  * preprocessing: max |difference| between CLIPImageProcessorPil's pixel_values and ours (reported separately, so
    drift is attributable to preprocessing vs. the graph);
  * embedding: cosine similarity of the final L2-normalized vectors. Reports the MINIMUM and exits non-zero if it
    is below --min-cosine (default 0.9999).
Needs torch + sentence-transformers (verification only, never the API).
"""

from __future__ import annotations

import argparse
import io
import os
import random
import statistics
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def cosine(a, b) -> float:
    import numpy as np

    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def fetch_catalog_images(candidates: int, seed: int, cache: Path) -> list[tuple[str, bytes]]:
    import httpx
    from dotenv import load_dotenv

    load_dotenv("backend/.env", override=True)
    from backend.category_quality import fetch_all
    from backend.supabase_compat import create_supabase_client

    client = create_supabase_client(os.environ["SUPABASE_URL"], os.getenv("SUPABASE_ANON_KEY") or os.environ["SUPABASE_KEY"])
    rows = [r for r in fetch_all(client, "products") if r.get("image_url")]
    picked = random.Random(seed).sample(rows, min(candidates, len(rows)))
    cache.mkdir(parents=True, exist_ok=True)

    def download(row):
        path = cache / f"{row['id']}.bin"
        if path.exists():
            return str(row["id"]), path.read_bytes()
        try:
            response = httpx.get(row["image_url"], timeout=30, follow_redirects=True)
            response.raise_for_status()
        except Exception:
            return None
        path.write_bytes(response.content)
        return str(row["id"]), response.content

    with ThreadPoolExecutor(8) as pool:
        return [r for r in pool.map(download, picked) if r]


def describe(data: bytes):
    from PIL import Image

    image = Image.open(io.BytesIO(data))
    return image.mode, image.size[0] / image.size[1], image.format


def select_diverse(items: list[tuple[str, bytes]], count: int, seed: int) -> list[tuple[str, bytes]]:
    info = {key: describe(data) for key, data in items}
    by_key = dict(items)
    chosen: list[str] = [k for k, (mode, _, _) in info.items() if mode != "RGB"]  # every non-RGB image found
    by_ratio = sorted(info, key=lambda k: info[k][1])
    for k in by_ratio[:12] + by_ratio[-12:]:  # the most extreme aspect ratios, tall and wide
        if k not in chosen:
            chosen.append(k)
    rest = [k for k in info if k not in chosen]
    random.Random(seed).shuffle(rest)
    chosen += rest[: max(0, count - len(chosen))]
    return [(k, by_key[k]) for k in chosen[:count]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--candidates", type=int, default=200)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--artifact-dir", default="backend/ml/artifacts/clip-vit-b-32-image")
    parser.add_argument("--cache-dir", type=Path, default=Path("/tmp/unfound_catalog_images"))
    parser.add_argument("--min-cosine", type=float, default=0.9999)
    args = parser.parse_args()

    import numpy as np
    from PIL import Image
    from sentence_transformers import SentenceTransformer

    from backend.ml.onnx_image_encoder import OnnxImageEncoder

    torch_model = SentenceTransformer(os.getenv("CLIP_MODEL_NAME", "clip-ViT-B-32"))
    processor = torch_model[0].processor.image_processor  # type: ignore[union-attr]
    onnx_encoder = OnnxImageEncoder(args.artifact_dir)

    downloaded = fetch_catalog_images(args.candidates, args.seed, args.cache_dir)
    selected = select_diverse(downloaded, args.count, args.seed)
    modes = Counter(describe(d)[0] for _, d in selected)
    ratios = [describe(d)[1] for _, d in selected]
    print(f"downloaded {len(downloaded)} real catalog images; selected {len(selected)}: modes={dict(modes)}  "
          f"aspect ratio (w/h) min={min(ratios):.2f} max={max(ratios):.2f}")

    cosines, pre_diffs, per_mode_pre = [], [], {}  # type: ignore[var-annotated]
    cases = []
    for key, data in selected:
        raw = Image.open(io.BytesIO(data))
        cases.append((key, raw.mode, raw.copy(), Image.open(io.BytesIO(data)).convert("RGB")))
    for key, mode, raw, rgb in cases:  # type: ignore[assignment]
        # the API's path: convert("RGB") first, then encode
        reference = torch_model.encode(rgb, normalize_embeddings=True)  # type: ignore[call-overload]
        candidate = onnx_encoder.encode(rgb)
        cosines.append((cosine(reference, candidate), key, mode, rgb.size))
        for label, image in (("api-path", rgb), ("raw-mode", raw)):  # raw-mode also exercises the processor's own RGB conversion
            expected = processor(images=image, return_tensors="np")["pixel_values"]  # type: ignore[operator]
            diff = float(np.abs(expected - onnx_encoder.preprocess(image)).max())
            pre_diffs.append(diff)
            per_mode_pre.setdefault(mode, []).append(diff)

    cosines.sort()
    print(f"EMBEDDING cosine: MIN = {cosines[0][0]:.9f}   mean = {statistics.mean(c[0] for c in cosines):.9f}   (gate >= {args.min_cosine})")
    print(f"  1 - min cosine = {1 - cosines[0][0]:.2e};  lowest 3: {[(f'{c:.9f}', m, s) for c, _, m, s in cosines[:3]]}")
    print(f"PREPROCESSING max |pixel_values difference| vs CLIPImageProcessorPil: overall {max(pre_diffs):.3e}  "
          f"({sum(1 for d in pre_diffs if d == 0)}/{len(pre_diffs)} tensors bit-identical)")
    print("  by original image mode:", {m: f"{max(v):.2e}" for m, v in per_mode_pre.items()})

    # Extra, clearly labelled: catalog images are all RGB-ish, so also convert real images into the other modes.
    extras = []
    for key, mode, raw, rgb in cases[:12]:  # type: ignore[assignment]
        for target in ("L", "P", "CMYK", "RGBA", "LA"):
            derived = rgb.convert(target)
            expected = processor(images=derived, return_tensors="np")["pixel_values"]  # type: ignore[operator]
            api_input = derived.convert("RGB")
            extras.append((target, float(np.abs(expected - onnx_encoder.preprocess(derived)).max()),
                           cosine(torch_model.encode(api_input, normalize_embeddings=True), onnx_encoder.encode(api_input))))  # type: ignore[call-overload]
    print(f"DERIVED-mode extras (real images re-encoded as L/P/CMYK/RGBA/LA, n={len(extras)}): max preprocessing diff "
          f"{max(e[1] for e in extras):.2e}, min cosine {min(e[2] for e in extras):.9f}")
    return 0 if cosines[0][0] >= args.min_cosine else 1


if __name__ == "__main__":
    sys.exit(main())
