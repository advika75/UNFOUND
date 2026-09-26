"""The torch-free text path: the API must start, and encode text, without ever importing torch."""

import ast
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REAL_ARTIFACT = ROOT / "backend" / "ml" / "artifacts" / "clip-vit-b-32-text"
HEAVY = ("torch", "transformers", "sentence_transformers", "sklearn")


def build_tiny_artifact(directory: Path) -> Path:
    """A real (tiny) ONNX model + tokenizer with the same I/O contract as the exported CLIP text tower."""
    np = pytest.importorskip("numpy")
    onnx = pytest.importorskip("onnx")
    tokenizers = pytest.importorskip("tokenizers")
    from onnx import TensorProto, helper, numpy_helper

    weights = numpy_helper.from_array((np.arange(512, dtype=np.float32) / 512.0 + 0.1).reshape(1, 512), name="w")
    graph = helper.make_graph(
        [
            helper.make_node("Cast", ["input_ids"], ["ids_f"], to=TensorProto.FLOAT),
            helper.make_node("ReduceMean", ["ids_f"], ["mean"], axes=[1], keepdims=1),
            helper.make_node("MatMul", ["mean", "w"], ["text_embeds"]),
        ],
        "tiny",
        [helper.make_tensor_value_info("input_ids", TensorProto.INT64, ["batch", "sequence"]),
         helper.make_tensor_value_info("attention_mask", TensorProto.INT64, ["batch", "sequence"])],
        [helper.make_tensor_value_info("text_embeds", TensorProto.FLOAT, ["batch", 512])],
        initializer=[weights],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 8
    directory.mkdir(parents=True, exist_ok=True)
    onnx.save(model, str(directory / "model.onnx"))

    vocab = {"[UNK]": 0, "kurti": 1, "black": 2, "top": 3}
    tok = tokenizers.Tokenizer(tokenizers.models.WordLevel(vocab, unk_token="[UNK]"))
    tok.pre_tokenizer = tokenizers.pre_tokenizers.Whitespace()
    tok.save(str(directory / "tokenizer.json"))
    (directory / "export_metadata.json").write_text(json.dumps({"embedding_dim": 512, "max_length": 77}))
    return directory


def run_snippet(code: str, *args: str) -> dict:
    """Run code in a fresh interpreter and return its last stdout line as JSON. Any test that needs a real
    onnxruntime session goes through here: in-process sessions race with onnxruntime's static destructors when
    the pytest process exits (observed as hangs/aborts on macOS)."""
    import os

    result = subprocess.run([sys.executable, "-c", textwrap.dedent(code), *args], cwd=ROOT, capture_output=True, text=True,
                            env={**os.environ, "PYTHONPATH": str(ROOT)}, timeout=180)
    assert result.returncode == 0, result.stderr[-1500:]
    return json.loads(result.stdout.strip().splitlines()[-1])


STARTUP_SCRIPT = textwrap.dedent(
    """
    import json, os, sys
    os.environ.update({"TEXT_ENCODER": "onnx", "ONNX_TEXT_ENCODER_DIR": sys.argv[1],
                       "SUPABASE_URL": "http://127.0.0.1:1", "SUPABASE_KEY": "test-key"})
    os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
    from fastapi.testclient import TestClient
    import backend.app as app_module
    with TestClient(app_module.app) as client:          # runs the real lifespan (model load + warm-up)
        vector = app_module.encode_text(client.app.state.clip_model, "kurti")   # the text-search hot path
        loaded = sorted(m for m in ("torch", "transformers", "sentence_transformers", "sklearn") if m in sys.modules)
        print(json.dumps({"heavy_modules_loaded": loaded, "dim": len(vector), "onnx_loaded": "onnxruntime" in sys.modules}))
    """
)


def run_startup(artifact: Path) -> dict:
    result = subprocess.run([sys.executable, "-c", STARTUP_SCRIPT, str(artifact)], cwd=ROOT, capture_output=True, text=True,
                            env={**__import__("os").environ, "PYTHONPATH": str(ROOT)}, timeout=120)
    assert result.returncode == 0, result.stderr[-1500:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_torch_is_absent_from_sys_modules_after_lifespan_and_a_text_encode(tmp_path):
    report = run_startup(build_tiny_artifact(tmp_path / "tiny"))
    assert report["heavy_modules_loaded"] == []
    assert report["dim"] == 512 and report["onnx_loaded"] is True


@pytest.mark.slow
@pytest.mark.skipif(not (REAL_ARTIFACT / "model.onnx").exists(), reason="ONNX artifact not built (python -m backend.ml.export_clip_text_onnx)")
def test_torch_is_absent_with_the_real_exported_artifact_too():
    report = run_startup(REAL_ARTIFACT)
    assert report["heavy_modules_loaded"] == []
    assert report["dim"] == 512


def test_no_module_on_the_api_path_imports_torch_at_top_level():
    files = [ROOT / "backend" / "app.py", ROOT / "backend" / "ml" / "onnx_text_encoder.py", *sorted((ROOT / "backend" / "search").glob("*.py"))]
    for path in files:
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            assert not [n for n in names if n.split(".")[0] in HEAVY], f"{path.name} imports {names} at top level"


def test_encoder_returns_a_unit_norm_float32_vector(tmp_path):
    artifact = build_tiny_artifact(tmp_path / "tiny")
    report = run_snippet(
        """
        import json, sys, numpy as np
        from backend.ml.onnx_text_encoder import OnnxTextEncoder
        enc = OnnxTextEncoder(sys.argv[1])
        v = enc.encode("black kurti"); raw = enc.encode("black kurti", normalize_embeddings=False)
        long_v = enc.encode(" ".join(["kurti"] * 300))
        print(json.dumps({"dtype": str(v.dtype), "shape": list(v.shape), "norm": float(np.linalg.norm(v)),
                          "raw_norm": float(np.linalg.norm(raw)), "tolist": len(v.tolist()), "long_len": len(long_v.tolist())}))
        """,
        str(artifact),
    )
    assert report["dtype"] == "float32" and report["shape"] == [512] and report["tolist"] == 512
    assert abs(report["norm"] - 1.0) < 1e-5 and abs(report["raw_norm"] - 1.0) > 1e-3
    assert report["long_len"] == 512  # >77 tokens is truncated, not a crash


def test_missing_artifact_fails_loudly_with_the_build_command(tmp_path):
    from backend.ml.onnx_text_encoder import OnnxTextEncoder

    with pytest.raises(FileNotFoundError, match="export_clip_text_onnx"):
        OnnxTextEncoder(tmp_path / "nothing-here")


def test_invalid_text_encoder_setting_is_rejected(monkeypatch):
    import backend.app as app_module

    monkeypatch.setattr(app_module, "TEXT_ENCODER", "banana")
    with pytest.raises(RuntimeError, match="onnx"):
        app_module.load_text_encoder()


def test_torch_mode_reuses_the_already_loaded_full_model_for_images(monkeypatch):
    import backend.app as app_module

    monkeypatch.setattr(app_module, "TEXT_ENCODER", "torch")
    monkeypatch.setattr(app_module, "IMAGE_ENCODER", "torch")
    sentinel = object()
    assert app_module.load_image_encoder(sentinel) is sentinel
