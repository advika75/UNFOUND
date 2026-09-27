# syntax=docker/dockerfile:1
#
# Lambda container image for the UNFOUND search API (arm64 / Graviton, Function URL,
# no VPC). Build from the repo root:
#
#   docker build -t unfound-api .
#
# ---------------------------------------------------------------------------------
# Why the ONNX artifacts are COPIED here, not exported by this Dockerfile:
#
# Exporting them needs backend/requirements.txt's full stack (torch + transformers +
# huggingface_hub, several GB) plus a live download from the Hugging Face Hub. Doing
# that on every image build would make builds slow, dependent on an external service
# being reachable, and non-deterministic byte-for-byte anyway -- the export isn't
# bit-reproducible run to run; what's actually verified is output correctness, via
# cosine similarity against a live torch forward pass (see backend/ml/verify_onnx_parity.py
# and verify_onnx_image_parity.py), not a hash match. So exporting is a separate,
# occasional step -- run once, verified once, then the resulting artifacts are reused
# as a build input, the same way you wouldn't recompile a compiler inside every build
# of the program it produces. Rebuild + verify with:
#
#   python -m backend.ml.export_clip_text_onnx
#   python -m backend.ml.export_clip_image_onnx
#   python -m backend.ml.verify_onnx_parity --queries-file <queries.json>
#   python -m backend.ml.verify_onnx_image_parity
#
# (needs backend/requirements.txt installed, and SUPABASE_URL / SUPABASE_KEY set for
# the catalog-sample checks). Both scripts exit non-zero if cosine similarity drops
# below 0.9999, so a bad export fails loudly before it ever reaches this Dockerfile.
# ---------------------------------------------------------------------------------

ARG PYTHON_VERSION=3.12

# ---- deps: install API-only requirements (no torch/transformers/sentence-transformers) ----
FROM --platform=linux/arm64 public.ecr.aws/lambda/python:${PYTHON_VERSION} AS deps
COPY backend/requirements-api.txt /tmp/requirements-api.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir mangum==0.22.0 -r /tmp/requirements-api.txt -t "${LAMBDA_TASK_ROOT}"

# ---- final: assemble the runtime image ----
FROM --platform=linux/arm64 public.ecr.aws/lambda/python:${PYTHON_VERSION}

COPY --from=deps ${LAMBDA_TASK_ROOT} ${LAMBDA_TASK_ROOT}

# Baked-in, pre-exported and correctness-verified ONNX artifacts (~581 MB combined).
# Copied first, as its own layer: it changes only when the model is re-exported, so
# ordinary code changes below don't invalidate this layer or re-send it to the registry.
COPY backend/ml/artifacts ${LAMBDA_TASK_ROOT}/backend/ml/artifacts

# Application code -- copied explicitly (not `COPY backend/`) so it never re-copies the
# artifacts directory above, keeping that large layer's cache intact across code edits.
COPY backend/__init__.py backend/*.py ${LAMBDA_TASK_ROOT}/backend/
COPY backend/ml/__init__.py backend/ml/*.py ${LAMBDA_TASK_ROOT}/backend/ml/
COPY backend/search ${LAMBDA_TASK_ROOT}/backend/search

ENV ENVIRONMENT=production
# Image-encoder loading stays lazy (default): the first image search in a container
# pays the load cost, not every cold start.

CMD ["backend.lambda_handler.handler"]
