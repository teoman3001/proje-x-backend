"""Dosya depolama: yerel disk veya Cloudflare R2 (S3 uyumlu)."""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Any

# Render genelde RENDER=1 veya true set eder
def _is_render() -> bool:
    v = os.environ.get("RENDER", "").lower()
    return v in ("1", "true", "yes")


def resolve_upload_dir() -> Path:
    """Yerel disk modu için dizin. Öncelik: UPLOAD_DIR env > Render /tmp > proje kökü/proje-x-files."""
    override = os.environ.get("UPLOAD_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if _is_render():
        return Path("/tmp/proje-x-files")
    return (Path(__file__).resolve().parent / "proje-x-files").resolve()


def ensure_local_upload_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def r2_settings() -> dict[str, str] | None:
    """Tüm R2 değişkenleri doluysa dict döner; eksikse None (yerel disk)."""
    account = os.environ.get("R2_ACCOUNT_ID", "").strip()
    key = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
    secret = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
    bucket = os.environ.get("R2_BUCKET_NAME", "").strip()
    if not all((account, key, secret, bucket)):
        return None
    return {
        "account_id": account,
        "access_key_id": key,
        "secret_access_key": secret,
        "bucket": bucket,
        "public_base": os.environ.get("R2_PUBLIC_BASE_URL", "").strip().rstrip("/"),
    }


def r2_client(cfg: dict[str, str]) -> Any:
    import boto3
    from botocore.config import Config

    endpoint = f"https://{cfg['account_id']}.r2.cloudflarestorage.com"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=cfg["access_key_id"],
        aws_secret_access_key=cfg["secret_access_key"],
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def r2_put_object(cfg: dict[str, str], key: str, body: bytes, content_type: str | None) -> None:
    client = r2_client(cfg)
    extra: dict[str, Any] = {}
    if content_type:
        extra["ContentType"] = content_type
    client.put_object(Bucket=cfg["bucket"], Key=key, Body=body, **extra)


def r2_upload_file_from_path(
    cfg: dict[str, str], local_path: str, key: str, content_type: str | None
) -> None:
    client = r2_client(cfg)
    extra: dict[str, Any] = {}
    if content_type:
        extra["ContentType"] = content_type
    if extra:
        client.upload_file(local_path, cfg["bucket"], key, ExtraArgs=extra)
    else:
        client.upload_file(local_path, cfg["bucket"], key)


def r2_get_object_stream(cfg: dict[str, str], key: str) -> tuple[Any, str | None]:
    client = r2_client(cfg)
    obj = client.get_object(Bucket=cfg["bucket"], Key=key)
    body = obj["Body"]
    ct = obj.get("ContentType")
    return body, ct


def guess_content_type(filename: str) -> str | None:
    ct, _ = mimetypes.guess_type(filename)
    return ct
