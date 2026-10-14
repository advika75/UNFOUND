"""One-time copy of product/brand images from Supabase Storage to Cloudflare R2.

Does NOT touch the database and does NOT delete anything from Supabase --
this only copies bytes to R2 and records a mapping of old -> new URL. The
actual products/brands.*_url rewrite is a separate, later SQL step (not run
by this script), so the old Supabase URLs stay valid for as long as you want.

Required env vars (read via load_dotenv from backend/.env; names only, never
printed by this script):
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY   -- to list bucket contents
  R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME,
  R2_PUBLIC_BASE_URL                         -- to write to R2

Usage:
  python -m backend.migrate_images_to_r2 --dry-run
  python -m backend.migrate_images_to_r2 [--bucket product-images|brand-images|both]

--dry-run makes zero calls to R2 and zero calls to download file content from
Supabase -- it only lists object metadata (same read-only call the earlier
storage census used) and reports what a real run would do.

Safety:
  - MAX_TOTAL_DOWNLOAD_BYTES hard-caps how much this process will download
    from Supabase in one run; it aborts with a clear message before crossing
    it, never mid-download.
  - DOWNLOAD_DELAY_SECONDS is a small pause between files, to stay gentle on
    Supabase's own egress/rate limits during the copy.
  - Resumable: data/r2_migration_map.json is read on startup, and any
    (bucket, path) already present with status "verified" is skipped
    entirely -- no re-download, no re-upload, no re-verification call.
  - The mapping file is rewritten after every single file, not batched at
    the end, so a crash mid-run loses at most the one in-flight file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv("backend/.env", override=True)

MAPPING_PATH = Path("data/r2_migration_map.json")
BUCKETS = ("product-images", "brand-images")
STORAGE_LIST_PAGE_SIZE = 1000

# Hard cap on bytes downloaded FROM SUPABASE in one run of this script. The
# full catalog is ~64 MB (measured via the earlier read-only census), so 100
# MB covers a complete copy with room to spare; this exists to protect
# Supabase egress headroom if the catalog has grown unexpectedly since.
MAX_TOTAL_DOWNLOAD_BYTES = 100 * 1024 * 1024

# Gentle pacing between downloads -- not a rate-limit workaround, just good
# citizenship against the same Supabase project the live app is still serving
# traffic from.
DOWNLOAD_DELAY_SECONDS = 0.2


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def _supabase_headers() -> dict[str, str]:
    key = _require_env("SUPABASE_SERVICE_ROLE_KEY")
    return {"apikey": key, "authorization": f"Bearer {key}", "content-type": "application/json"}


def list_bucket_objects(supabase_url: str, bucket: str) -> list[dict[str, Any]]:
    """Read-only: object name + size metadata, recursing one level into each
    brand-id folder. No file content is fetched here."""
    headers = _supabase_headers()

    def list_prefix(prefix: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        offset = 0
        while True:
            resp = httpx.post(
                f"{supabase_url}/storage/v1/object/list/{bucket}",
                json={"prefix": prefix, "limit": STORAGE_LIST_PAGE_SIZE, "offset": offset,
                      "sortBy": {"column": "name", "order": "asc"}},
                headers=headers, timeout=30,
            )
            resp.raise_for_status()
            page = resp.json()
            items.extend(page)
            if len(page) < STORAGE_LIST_PAGE_SIZE:
                break
            offset += STORAGE_LIST_PAGE_SIZE
        return items

    top = list_prefix("")
    folders = [item["name"] for item in top if item.get("id") is None]
    files = [{"path": item["name"], "size": (item.get("metadata") or {}).get("size", 0)}
             for item in top if item.get("id") is not None]
    for folder in folders:
        for item in list_prefix(folder + "/"):
            if item.get("id") is not None:
                files.append({"path": f"{folder}/{item['name']}", "size": (item.get("metadata") or {}).get("size", 0)})
    return files


def load_mapping() -> dict[str, Any]:
    if MAPPING_PATH.exists():
        return json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    return {}


def save_mapping(mapping: dict[str, Any]) -> None:
    MAPPING_PATH.parent.mkdir(parents=True, exist_ok=True)
    MAPPING_PATH.write_text(json.dumps(mapping, indent=2, sort_keys=True), encoding="utf-8")


def mapping_key(bucket: str, path: str) -> str:
    return f"{bucket}/{path}"


def get_r2_client():
    import boto3

    account_id = _require_env("R2_ACCOUNT_ID")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=_require_env("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=_require_env("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
    )


def copy_one_file(
    *, bucket: str, path: str, size: int, supabase_url: str, r2_client: Any,
    r2_bucket: str, r2_public_base_url: str,
) -> dict[str, Any]:
    old_url = f"{supabase_url}/storage/v1/object/public/{bucket}/{path}"
    response = httpx.get(old_url, timeout=60)
    response.raise_for_status()
    content = response.content

    sha256 = hashlib.sha256(content).hexdigest()
    md5 = hashlib.md5(content).hexdigest()  # noqa: S324 -- not for security, just matches R2/S3's ETag for a single PutObject

    r2_key = f"{bucket}/{path}"
    r2_client.put_object(Bucket=r2_bucket, Key=r2_key, Body=content, ContentType=response.headers.get("content-type", "application/octet-stream"))

    head = r2_client.head_object(Bucket=r2_bucket, Key=r2_key)
    uploaded_size = head["ContentLength"]
    uploaded_etag = head["ETag"].strip('"').lower()
    verified = uploaded_size == len(content) and uploaded_etag == md5

    new_url = f"{r2_public_base_url.rstrip('/')}/{r2_key}"
    return {
        "old_url": old_url,
        "new_url": new_url,
        "bytes": len(content),
        "sha256": sha256,
        "etag_md5": md5,
        "verified": verified,
        "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def run(buckets: list[str], dry_run: bool) -> int:
    supabase_url = _require_env("SUPABASE_URL")
    mapping = load_mapping()

    all_files: list[tuple[str, dict[str, Any]]] = []
    for bucket in buckets:
        for item in list_bucket_objects(supabase_url, bucket):
            all_files.append((bucket, item))

    total_bytes_catalog = sum(item["size"] for _bucket, item in all_files)
    already_done = sum(
        1 for bucket, item in all_files
        if mapping.get(mapping_key(bucket, item["path"]), {}).get("verified")
    )
    remaining = [
        (bucket, item) for bucket, item in all_files
        if not mapping.get(mapping_key(bucket, item["path"]), {}).get("verified")
    ]
    remaining_bytes = sum(item["size"] for _bucket, item in remaining)

    print(f"Buckets: {buckets}")
    print(f"Total files in catalog: {len(all_files)} ({total_bytes_catalog:,} bytes)")
    print(f"Already verified (resumable, will be skipped): {already_done}")
    print(f"Remaining to copy: {len(remaining)} ({remaining_bytes:,} bytes)")

    if dry_run:
        print("\n--dry-run: no calls made to bucket content or R2. Sample of what would be copied:")
        for bucket, item in remaining[:10]:
            print(f"  {bucket}/{item['path']}  ({item['size']:,} bytes)")
        if remaining_bytes > MAX_TOTAL_DOWNLOAD_BYTES:
            print(f"\nWARNING: a real run would exceed MAX_TOTAL_DOWNLOAD_BYTES "
                  f"({remaining_bytes:,} > {MAX_TOTAL_DOWNLOAD_BYTES:,}) and would abort partway through.")
        return 0

    r2_client = get_r2_client()
    r2_bucket = _require_env("R2_BUCKET_NAME")
    r2_public_base_url = _require_env("R2_PUBLIC_BASE_URL")

    downloaded_this_run = 0
    for bucket, item in remaining:
        if downloaded_this_run + item["size"] > MAX_TOTAL_DOWNLOAD_BYTES:
            print(
                f"\nABORTING before downloading {bucket}/{item['path']}: this run has already "
                f"downloaded {downloaded_this_run:,} bytes, and this file ({item['size']:,} bytes) "
                f"would exceed the {MAX_TOTAL_DOWNLOAD_BYTES:,}-byte cap for a single run. "
                f"Already-verified files are safe and were not re-touched; re-run this script "
                f"to resume from here."
            )
            save_mapping(mapping)
            return 1

        result = copy_one_file(
            bucket=bucket, path=item["path"], size=item["size"], supabase_url=supabase_url,
            r2_client=r2_client, r2_bucket=r2_bucket, r2_public_base_url=r2_public_base_url,
        )
        downloaded_this_run += result["bytes"]
        mapping[mapping_key(bucket, item["path"])] = result
        save_mapping(mapping)

        status = "OK" if result["verified"] else "VERIFICATION MISMATCH"
        print(f"  [{status}] {bucket}/{item['path']} -> {result['new_url']} ({result['bytes']:,} bytes)")
        if not result["verified"]:
            print("    size/etag did not match after upload -- left in the mapping file as unverified for review.")

        time.sleep(DOWNLOAD_DELAY_SECONDS)

    print(f"\nDone. Downloaded {downloaded_this_run:,} bytes this run.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.migrate_images_to_r2")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--bucket", choices=["product-images", "brand-images", "both"], default="both")
    args = parser.parse_args(argv)
    buckets = list(BUCKETS) if args.bucket == "both" else [args.bucket]
    return run(buckets, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
