"""Torch-free CLIP image encoder for image-upload search.

Imports only PIL, numpy and onnxruntime (never torch/transformers/sentence-transformers). Preprocessing
mirrors transformers' CLIPImageProcessorPil (the exact implementation sentence-transformers uses on this
machine: PIL backend, no torchvision), step for step, reading its parameters from the artifact's
preprocessor_config.json:

  step                 config value        what the reference does (transformers.image_transforms / PilBackend)
  -------------------  ------------------  ------------------------------------------------------------------
  convert to RGB       do_convert_rgb      image.convert("RGB") unless already RGB
  resize               size=224 (shortest  new_short=224, new_long=int(224 * long / short)  (int() TRUNCATES),
                       edge)               then PIL Image.resize((w, h), resample=..., reducing_gap=None)
  resample             resample=3          PIL BICUBIC (PIL's resample ints are 0..5 == PIL.Image.Resampling)
  center crop          crop_size=224       top=(H-224)//2, left=(W-224)//2  (floor division; no rounding)
  rescale              (default 1/255)     (uint8.astype(float64) * (1/255)).astype(float32)
  normalize            image_mean/std      ((x.T - mean) / std).T with mean/std as float32 arrays
  channel order        (default)           RGB, channels-first (C, H, W), then a batch axis

The graph is exported by backend/ml/export_clip_image_onnx.py; L2 normalization happens here, in float32,
where sentence-transformers applies it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

EMBEDDING_DIM = 512
RESCALE_FACTOR = 1 / 255  # transformers' default rescale_factor (not stored in preprocessor_config.json)


class ClipImagePreprocessor:
    """PIL/numpy preprocessing only (no onnxruntime), mirroring CLIPImageProcessorPil; see the module docstring."""

    def __init__(self, config: dict[str, Any]) -> None:
        import numpy as np
        from PIL import Image

        # Only the configuration this port was verified against is accepted; anything else must fail loudly
        # rather than silently produce different pixels.
        for key, expected in (("do_resize", True), ("do_center_crop", True), ("do_normalize", True)):
            if config.get(key, expected) is not expected:
                raise ValueError(f"unsupported preprocessor_config: {key}={config.get(key)!r}")
        size, crop = config["size"], config["crop_size"]
        if not isinstance(size, int) or not isinstance(crop, int):
            raise ValueError(f"unsupported preprocessor_config: size={size!r} crop_size={crop!r} (expected ints)")
        self._np = np
        self._size, self._crop = size, crop
        self._resample = Image.Resampling(int(config["resample"]))
        self._mean = np.array(config["image_mean"], dtype=np.float32)
        self._std = np.array(config["image_std"], dtype=np.float32)

    def __call__(self, image: Any) -> Any:
        """PIL image -> float32 array of shape (1, 3, crop, crop), identical to CLIPImageProcessorPil's pixel_values."""
        np = self._np
        if image.mode != "RGB":  # do_convert_rgb
            image = image.convert("RGB")
        width, height = image.size
        short, long = (width, height) if width <= height else (height, width)
        new_short, new_long = self._size, int(self._size * long / short)  # int() truncates, as in the reference
        new_width, new_height = (new_short, new_long) if width <= height else (new_long, new_short)
        resized = image.resize((new_width, new_height), resample=self._resample, reducing_gap=None)
        chw = np.array(resized).transpose(2, 0, 1)  # HWC uint8 -> CHW
        top, left = (chw.shape[1] - self._crop) // 2, (chw.shape[2] - self._crop) // 2
        chw = chw[:, top : top + self._crop, left : left + self._crop]
        chw = (chw.astype(np.float64) * RESCALE_FACTOR).astype(np.float32)  # rescale: upcast, multiply, downcast
        chw = ((chw.T - self._mean) / self._std).T  # normalize in float32
        return np.ascontiguousarray(chw[None], dtype=np.float32)


class OnnxImageEncoder:
    def __init__(self, artifact_dir: str | Path, *, intra_op_threads: int | None = None) -> None:
        import numpy as np

        directory = Path(artifact_dir)
        for name in ("model.onnx", "preprocessor_config.json", "export_metadata.json"):
            if not (directory / name).is_file():
                raise FileNotFoundError(
                    f"{directory / name} is missing. Build it with `python -m backend.ml.export_clip_image_onnx` "
                    "(needs torch; run at build time, not in the API image) or set ONNX_IMAGE_ENCODER_DIR."
                )
        metadata = json.loads((directory / "export_metadata.json").read_text(encoding="utf-8"))
        if metadata.get("embedding_dim") != EMBEDDING_DIM:
            raise ValueError(f"unexpected embedding_dim in export metadata: {metadata.get('embedding_dim')}")
        self._preprocessor = ClipImagePreprocessor(json.loads((directory / "preprocessor_config.json").read_text(encoding="utf-8")))
        self.metadata: dict[str, Any] = metadata
        self._np = np

        import onnxruntime as ort  # imported late: nothing above (file/config checks) needs it

        ort.disable_telemetry_events()  # no phone-home from a server; also avoids a telemetry-thread shutdown race
        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if intra_op_threads:
            options.intra_op_num_threads = intra_op_threads
        self._session = ort.InferenceSession(str(directory / "model.onnx"), sess_options=options, providers=["CPUExecutionProvider"])

    def preprocess(self, image: Any) -> Any:
        return self._preprocessor(image)

    def encode(self, image: Any, normalize_embeddings: bool = True) -> Any:
        np = self._np
        (embedding,) = self._session.run(["image_embeds"], {"pixel_values": self.preprocess(image)})
        vector = embedding[0].astype(np.float32, copy=False)
        if normalize_embeddings:
            vector = vector / np.maximum(np.linalg.norm(vector), np.float32(1e-12))
        return vector
