from __future__ import annotations

from fastapi import UploadFile


MAX_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


class UploadValidationError(ValueError):
    pass


def require_single_search_input(*, text_query: str | None, image_file: UploadFile | None) -> None:
    has_text = bool(text_query and text_query.strip())
    has_image = image_file is not None
    if has_text == has_image:
        raise UploadValidationError(
            "Send exactly one search input: either text_query or image_file."
        )


async def read_validated_image_upload(image_file: UploadFile) -> bytes:
    content_type = (image_file.content_type or "").lower()
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise UploadValidationError("Upload a JPEG, PNG, or WebP image.")
    content = await image_file.read(MAX_IMAGE_BYTES + 1)
    if not content:
        raise UploadValidationError("The uploaded image is empty.")
    if len(content) > MAX_IMAGE_BYTES:
        raise UploadValidationError("The uploaded image exceeds the 10 MB limit.")
    return content
