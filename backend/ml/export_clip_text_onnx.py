"""Export the CLIP text encoder to ONNX for torch-free query-time serving.

Run once (needs torch + transformers + onnx; NOT needed by the API at runtime):

    python -m backend.ml.export_clip_text_onnx [--out-dir backend/ml/artifacts/clip-vit-b-32-text]

Writes to the output dir:
  model.onnx            text tower + text_projection; input_ids/attention_mask -> unnormalized 512-d embedding
  tokenizer.json        the exact CLIPTokenizer (BPE, lowercasing, BOS/EOS) serialized for the `tokenizers` library
  export_metadata.json  opset, versions, source model revision, sha256s, shapes -- the pin/record of how it was built

L2 normalization is deliberately NOT in the graph: the serving code does it in numpy exactly where
sentence-transformers does (encode(..., normalize_embeddings=True)), so the graph output is directly
comparable to CLIPModel.get_text_features.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

MODEL_ID = "sentence-transformers/clip-ViT-B-32"
OPSET = 17
MAX_LENGTH = 77
DEFAULT_OUT = Path(__file__).parent / "artifacts" / "clip-vit-b-32-text"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export(out_dir: Path) -> dict:
    import onnx
    import onnxruntime
    import torch
    import transformers
    from huggingface_hub import snapshot_download
    from transformers import CLIPModel, CLIPTokenizerFast

    snapshot = Path(snapshot_download(MODEL_ID))
    clip_dir = snapshot / "0_CLIPModel"
    clip = CLIPModel.from_pretrained(clip_dir).eval()
    tokenizer = CLIPTokenizerFast.from_pretrained(clip_dir)

    class TextTower(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.text_model, self.projection = clip.text_model, clip.text_projection

        def forward(self, input_ids: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
            pooled = self.text_model(input_ids=input_ids, attention_mask=attention_mask).pooler_output
            return self.projection(pooled)

    out_dir.mkdir(parents=True, exist_ok=True)
    model_path, tokenizer_path = out_dir / "model.onnx", out_dir / "tokenizer.json"
    sample = tokenizer(["black oversized top"], return_tensors="pt", padding=True)
    torch.onnx.export(
        TextTower().eval(),
        (sample["input_ids"], sample["attention_mask"]),
        str(model_path),
        input_names=["input_ids", "attention_mask"],
        output_names=["text_embeds"],
        dynamic_axes={"input_ids": {0: "batch", 1: "sequence"}, "attention_mask": {0: "batch", 1: "sequence"}, "text_embeds": {0: "batch"}},
        opset_version=OPSET,
        do_constant_folding=True,
        dynamo=False,
    )
    onnx.checker.check_model(str(model_path))
    tokenizer.backend_tokenizer.save(str(tokenizer_path))

    metadata = {
        "source_model": MODEL_ID,
        "source_revision": snapshot.name,
        "onnx_opset": OPSET,
        "onnx_ir_version": onnx.load(str(model_path), load_external_data=False).ir_version,
        "inputs": ["input_ids:int64[batch,sequence]", "attention_mask:int64[batch,sequence]"],
        "output": "text_embeds:float32[batch,512] (unnormalized; normalize with L2 in the caller)",
        "max_length": MAX_LENGTH,
        "embedding_dim": 512,
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "onnx": onnx.__version__,
        "onnxruntime": onnxruntime.__version__,
        "model_onnx_sha256": sha256(model_path),
        "model_onnx_bytes": model_path.stat().st_size,
        "tokenizer_json_sha256": sha256(tokenizer_path),
    }
    (out_dir / "export_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    metadata = export(parser.parse_args().out_dir)
    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
