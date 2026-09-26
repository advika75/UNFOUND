"""Torch-free CLIP text encoder for the API's query-time path.

Imports only numpy, tokenizers and onnxruntime -- never torch, transformers or sentence-transformers,
so the API can start (and a Lambda can init) without paying for or even installing them. The artifact
is produced by backend/ml/export_clip_text_onnx.py.

Deliberately mirrors the small slice of the SentenceTransformer interface the app uses:
`encode(text, normalize_embeddings=True)` returns a float32 numpy vector (has .tolist()), so
backend.app.encode_text works unchanged with either backend.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MAX_LENGTH = 77
EMBEDDING_DIM = 512


class OnnxTextEncoder:
    def __init__(self, artifact_dir: str | Path, *, intra_op_threads: int | None = None) -> None:
        import numpy as np
        from tokenizers import Tokenizer

        directory = Path(artifact_dir)
        for name in ("model.onnx", "tokenizer.json", "export_metadata.json"):
            if not (directory / name).is_file():
                raise FileNotFoundError(
                    f"{directory / name} is missing. Build it with `python -m backend.ml.export_clip_text_onnx` "
                    "(needs torch; run at build time, not in the API image) or set ONNX_TEXT_ENCODER_DIR."
                )
        self.metadata: dict[str, Any] = json.loads((directory / "export_metadata.json").read_text(encoding="utf-8"))
        if self.metadata.get("embedding_dim") != EMBEDDING_DIM:
            raise ValueError(f"unexpected embedding_dim in export metadata: {self.metadata.get('embedding_dim')}")

        self._np = np
        self._tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        # Sequences longer than the model's 77 positions would error in torch; truncating (keeping BOS/EOS) is the
        # only behavioral difference, and only for inputs the torch path could not encode at all.
        self._tokenizer.enable_truncation(max_length=int(self.metadata.get("max_length", MAX_LENGTH)))

        import onnxruntime as ort  # imported late: nothing above (file/metadata checks) needs it

        ort.disable_telemetry_events()  # no phone-home from a server; also avoids a telemetry-thread shutdown race
        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if intra_op_threads:
            options.intra_op_num_threads = intra_op_threads
        self._session = ort.InferenceSession(str(directory / "model.onnx"), sess_options=options, providers=["CPUExecutionProvider"])

    def encode(self, text: str, normalize_embeddings: bool = True) -> Any:
        np = self._np
        encoding = self._tokenizer.encode(text)
        input_ids = np.asarray([encoding.ids], dtype=np.int64)
        attention_mask = np.asarray([encoding.attention_mask], dtype=np.int64)
        (embedding,) = self._session.run(["text_embeds"], {"input_ids": input_ids, "attention_mask": attention_mask})
        vector = embedding[0].astype(np.float32, copy=False)
        if normalize_embeddings:
            # same as torch.nn.functional.normalize: x / max(||x||, 1e-12), in float32
            vector = vector / np.maximum(np.linalg.norm(vector), np.float32(1e-12))
        return vector
