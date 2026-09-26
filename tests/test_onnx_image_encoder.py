"""The torch-free image path: preprocessing must match CLIPImageProcessorPil exactly, and the API must
encode uploaded images without ever importing torch."""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REAL_IMAGE_ARTIFACT = ROOT / "backend" / "ml" / "artifacts" / "clip-vit-b-32-image"
REAL_TEXT_ARTIFACT = ROOT / "backend" / "ml" / "artifacts" / "clip-vit-b-32-text"

PREPROCESSOR_CONFIG = {
    "crop_size": 224, "do_center_crop": True, "do_normalize": True, "do_resize": True,
    "image_mean": [0.48145466, 0.4578275, 0.40821073], "image_std": [0.26862954, 0.26130258, 0.27577711],
    "resample": 3, "size": 224,
}


def build_tiny_image_artifact(directory: Path) -> Path:
    """A real (tiny) ONNX model with the same I/O contract as the exported CLIP image tower."""
    np = pytest.importorskip("numpy")
    onnx = pytest.importorskip("onnx")
    from onnx import TensorProto, helper, numpy_helper

    weights = numpy_helper.from_array((np.arange(512, dtype=np.float32) / 512.0 + 0.1).reshape(1, 512), name="w")
    graph = helper.make_graph(
        [helper.make_node("ReduceMean", ["pixel_values"], ["m"], axes=[1, 2, 3], keepdims=0),
         helper.make_node("Unsqueeze", ["m", "axes"], ["m2"]),
         helper.make_node("MatMul", ["m2", "w"], ["image_embeds"])],
        "tiny_image",
        [helper.make_tensor_value_info("pixel_values", TensorProto.FLOAT, ["batch", 3, 224, 224])],
        [helper.make_tensor_value_info("image_embeds", TensorProto.FLOAT, ["batch", 512])],
        initializer=[weights, numpy_helper.from_array(np.array([1], dtype=np.int64), name="axes")],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 8
    directory.mkdir(parents=True, exist_ok=True)
    onnx.save(model, str(directory / "model.onnx"))
    (directory / "preprocessor_config.json").write_text(json.dumps(PREPROCESSOR_CONFIG))
    (directory / "export_metadata.json").write_text(json.dumps({"embedding_dim": 512}))
    return directory


def make_images():
    np = pytest.importorskip("numpy")
    from PIL import Image

    rng = np.random.default_rng(3)
    sizes = [(640, 640), (300, 480), (480, 300), (1000, 300), (300, 1000), (224, 224), (225, 224), (224, 225),
             (50, 40), (40, 50), (223, 500), (1, 224), (224, 1), (333, 331), (2048, 1365)]
    images = [(f"{w}x{h}", Image.fromarray(rng.integers(0, 256, (h, w, 3), dtype=np.uint8))) for w, h in sizes]
    base = images[1][1]
    images += [(f"mode-{mode}", base.convert(mode)) for mode in ("L", "P", "CMYK", "RGBA", "LA", "1")]
    return images


def test_preprocessing_is_bit_identical_to_clip_image_processor_pil(tmp_path):
    """Landscape, portrait, tiny, exact-size, off-by-one, extreme ratios, and every colour mode."""
    np = pytest.importorskip("numpy")
    pytest.importorskip("transformers")
    from huggingface_hub import try_to_load_from_cache  # noqa: F401 -- reference processor needs no network
    from transformers.models.clip.image_processing_pil_clip import CLIPImageProcessorPil

    from backend.ml.onnx_image_encoder import ClipImagePreprocessor

    preprocess = ClipImagePreprocessor(PREPROCESSOR_CONFIG)
    processor = CLIPImageProcessorPil(**PREPROCESSOR_CONFIG)
    for name, image in make_images():
        expected = processor(images=image, return_tensors="np")["pixel_values"]
        assert np.array_equal(expected, preprocess(image)), name


def test_preprocess_output_contract(tmp_path):
    np = pytest.importorskip("numpy")
    from PIL import Image

    from backend.ml.onnx_image_encoder import ClipImagePreprocessor

    pixels = ClipImagePreprocessor(PREPROCESSOR_CONFIG)(Image.new("RGB", (500, 300), (10, 200, 30)))
    assert pixels.shape == (1, 3, 224, 224) and pixels.dtype == np.float32 and pixels.flags["C_CONTIGUOUS"]


def test_encoder_returns_a_unit_norm_float32_vector(tmp_path):
    from tests.test_onnx_text_encoder import run_snippet

    report = run_snippet(
        """
        import json, sys, numpy as np
        from PIL import Image
        from backend.ml.onnx_image_encoder import OnnxImageEncoder
        v = OnnxImageEncoder(sys.argv[1]).encode(Image.new("RGB", (300, 200), (90, 30, 200)))
        print(json.dumps({"dtype": str(v.dtype), "shape": list(v.shape), "norm": float(np.linalg.norm(v))}))
        """,
        str(build_tiny_image_artifact(tmp_path / "img")),
    )
    assert report["dtype"] == "float32" and report["shape"] == [512] and abs(report["norm"] - 1.0) < 1e-5


def test_unsupported_preprocessor_config_fails_loudly_instead_of_producing_different_pixels(tmp_path):
    from backend.ml.onnx_image_encoder import OnnxImageEncoder

    directory = build_tiny_image_artifact(tmp_path / "img")
    bad = {**PREPROCESSOR_CONFIG, "size": {"height": 224, "width": 224}}
    (directory / "preprocessor_config.json").write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="unsupported preprocessor_config"):
        OnnxImageEncoder(directory)
    (directory / "preprocessor_config.json").write_text(json.dumps({**PREPROCESSOR_CONFIG, "do_normalize": False}))
    with pytest.raises(ValueError, match="do_normalize"):
        OnnxImageEncoder(directory)


def test_missing_artifact_fails_loudly_with_the_build_command(tmp_path):
    from backend.ml.onnx_image_encoder import OnnxImageEncoder

    with pytest.raises(FileNotFoundError, match="export_clip_image_onnx"):
        OnnxImageEncoder(tmp_path / "nothing-here")


def test_startup_fails_loudly_when_the_image_artifact_is_missing(monkeypatch, tmp_path):
    import backend.app as app_module

    monkeypatch.setattr(app_module, "IMAGE_ENCODER", "onnx")
    monkeypatch.setattr(app_module, "ONNX_IMAGE_ENCODER_DIR", str(tmp_path / "absent"))
    with pytest.raises(FileNotFoundError, match="export_clip_image_onnx"):
        app_module.check_image_encoder_artifact()
    monkeypatch.setattr(app_module, "IMAGE_ENCODER", "torch")
    app_module.check_image_encoder_artifact()  # torch mode has no artifact to check


def test_image_encoder_is_loaded_once_and_lazily(monkeypatch):
    import backend.app as app_module

    loads = []
    monkeypatch.setattr(app_module, "_IMAGE_ENCODER", {})
    monkeypatch.setattr(app_module, "load_image_encoder", lambda text_encoder=None: loads.append(1) or object())
    first, second = app_module.get_image_encoder(), app_module.get_image_encoder()
    assert first is second and loads == [1]


def test_invalid_image_encoder_setting_is_rejected(monkeypatch):
    import backend.app as app_module

    monkeypatch.setattr(app_module, "IMAGE_ENCODER", "banana")
    with pytest.raises(RuntimeError, match="onnx"):
        app_module.load_image_encoder()


def test_the_old_503_fallback_for_a_missing_torch_stack_is_gone():
    source = (ROOT / "backend" / "app.py").read_text(encoding="utf-8")
    assert "Image search is not available" not in source


IMAGE_STARTUP_SCRIPT = textwrap.dedent(
    """
    import io, json, os, sys
    os.environ.update({"TEXT_ENCODER": "onnx", "IMAGE_ENCODER": "onnx", "ONNX_TEXT_ENCODER_DIR": sys.argv[1],
                       "ONNX_IMAGE_ENCODER_DIR": sys.argv[2], "EAGER_IMAGE_ENCODER": sys.argv[3],
                       "SUPABASE_URL": "http://127.0.0.1:1", "SUPABASE_KEY": "test-key"})
    os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
    from PIL import Image
    from fastapi.testclient import TestClient
    import backend.app as app_module
    with TestClient(app_module.app) as client:
        eager_loaded = "model" in app_module._IMAGE_ENCODER
        buffer = io.BytesIO(); Image.new("RGB", (400, 300), (120, 40, 200)).save(buffer, "JPEG")
        vector = app_module.encode_image(app_module.get_image_encoder(client.app.state.clip_model), buffer.getvalue())
        loaded = sorted(m for m in ("torch", "transformers", "sentence_transformers", "sklearn") if m in sys.modules)
        print(json.dumps({"heavy_modules_loaded": loaded, "dim": len(vector), "loaded_at_startup": eager_loaded}))
    """
)


def run_image_startup(text_dir: Path, image_dir: Path, eager: str) -> dict:
    import os

    result = subprocess.run([sys.executable, "-c", IMAGE_STARTUP_SCRIPT, str(text_dir), str(image_dir), eager], cwd=ROOT,
                            capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(ROOT)}, timeout=180)
    assert result.returncode == 0, result.stderr[-1500:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def _tiny_text_artifact(tmp_path):
    from tests.test_onnx_text_encoder import build_tiny_artifact

    return build_tiny_artifact(tmp_path / "text")


@pytest.mark.parametrize("eager", ["0", "1"])
def test_torch_is_absent_after_startup_and_an_image_encode_lazy_or_eager(tmp_path, eager):
    report = run_image_startup(_tiny_text_artifact(tmp_path), build_tiny_image_artifact(tmp_path / "img"), eager)
    assert report["heavy_modules_loaded"] == [] and report["dim"] == 512
    assert report["loaded_at_startup"] is (eager == "1")


@pytest.mark.slow
@pytest.mark.skipif(not ((REAL_IMAGE_ARTIFACT / "model.onnx").exists() and (REAL_TEXT_ARTIFACT / "model.onnx").exists()),
                    reason="ONNX artifacts not built")
def test_torch_is_absent_with_the_real_artifacts_for_both_text_and_image():
    report = run_image_startup(REAL_TEXT_ARTIFACT, REAL_IMAGE_ARTIFACT, "0")
    assert report["heavy_modules_loaded"] == [] and report["dim"] == 512


def test_no_module_on_the_api_path_imports_torch_at_top_level_including_the_image_encoder():
    import ast

    for path in (ROOT / "backend" / "ml" / "onnx_image_encoder.py", ROOT / "backend" / "ml" / "onnx_text_encoder.py"):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            assert not [n for n in names if n.split(".")[0] in ("torch", "transformers", "sentence_transformers", "sklearn")], path.name
