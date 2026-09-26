"""Export the CLIP image encoder (vision tower + visual_projection) to ONNX for torch-free serving.

Run once (needs torch + transformers + onnx; NOT needed by the API at runtime):

    python -m backend.ml.export_clip_image_onnx [--out-dir backend/ml/artifacts/clip-vit-b-32-image]

Writes to the output dir:
  model.onnx               pixel_values float32[batch,3,224,224] -> unnormalized 512-d embedding
  preprocessor_config.json the source model's CLIPImageProcessor config, copied verbatim; the serving code
                           (backend/ml/onnx_image_encoder.py) reads its resize/crop/normalize values from HERE
  export_metadata.json     opset, versions, source revision, sha256s, shapes

Same opset and conventions as the text export. L2 normalization is applied by the caller, exactly where
sentence-transformers applies it (encode(..., normalize_embeddings=True)).
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from backend.ml.export_clip_text_onnx import MODEL_ID, OPSET, sha256

DEFAULT_OUT = Path(__file__).parent / "artifacts" / "clip-vit-b-32-image"


def export(out_dir: Path) -> dict:
    import onnx
    import onnxruntime
    import torch
    import transformers
    from huggingface_hub import snapshot_download
    from transformers import CLIPModel

    snapshot = Path(snapshot_download(MODEL_ID))
    clip_dir = snapshot / "0_CLIPModel"
    clip = CLIPModel.from_pretrained(clip_dir).eval()

    class ImageTower(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.vision_model, self.projection = clip.vision_model, clip.visual_projection

        def forward(self, pixel_values: torch.Tensor) -> torch.Tensor:
            return self.projection(self.vision_model(pixel_values=pixel_values).pooler_output)

    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / "model.onnx"
    torch.onnx.export(
        ImageTower().eval(),
        (torch.zeros(1, 3, 224, 224),),
        str(model_path),
        input_names=["pixel_values"],
        output_names=["image_embeds"],
        dynamic_axes={"pixel_values": {0: "batch"}, "image_embeds": {0: "batch"}},
        opset_version=OPSET,
        do_constant_folding=True,
        dynamo=False,
    )
    onnx.checker.check_model(str(model_path))
    config_path = out_dir / "preprocessor_config.json"
    shutil.copyfile(clip_dir / "preprocessor_config.json", config_path)

    metadata = {
        "source_model": MODEL_ID,
        "source_revision": snapshot.name,
        "onnx_opset": OPSET,
        "onnx_ir_version": onnx.load(str(model_path), load_external_data=False).ir_version,
        "inputs": ["pixel_values:float32[batch,3,224,224]"],
        "output": "image_embeds:float32[batch,512] (unnormalized; normalize with L2 in the caller)",
        "embedding_dim": 512,
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "onnx": onnx.__version__,
        "onnxruntime": onnxruntime.__version__,
        "model_onnx_sha256": sha256(model_path),
        "model_onnx_bytes": model_path.stat().st_size,
        "preprocessor_config_sha256": sha256(config_path),
    }
    (out_dir / "export_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    print(json.dumps(export(parser.parse_args().out_dir), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
