from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

import boto3
from botocore.exceptions import BotoCoreError, ClientError

if TYPE_CHECKING:
    from .config import Config


logger = logging.getLogger(__name__)


class UploadError(RuntimeError):
    pass


class R2Uploader:
    def __init__(
        self,
        account_id: str,
        access_key_id: str,
        secret_access_key: str,
        bucket: str,
        public_base_url: str = "",
    ) -> None:
        self._bucket = bucket
        self._public_base_url = public_base_url.rstrip("/")
        self._client = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
        )

    def upload_file(self, local_path: Path, key: str) -> str | None:
        try:
            self._client.upload_file(str(local_path), self._bucket, key)
        except (BotoCoreError, ClientError) as e:
            raise UploadError(
                f"r2 upload failed for key={key}: {type(e).__name__}"
            ) from None
        url = f"{self._public_base_url}/{key}" if self._public_base_url else None
        logger.info("r2_upload_ok key=%s url=%s", key, url or "(no public_base_url)")
        return url

    def upload_bytes(
        self, data: bytes, key: str, content_type: str = "application/octet-stream"
    ) -> str | None:
        try:
            self._client.put_object(
                Bucket=self._bucket, Key=key, Body=data, ContentType=content_type,
            )
        except (BotoCoreError, ClientError) as e:
            raise UploadError(
                f"r2 upload failed for key={key}: {type(e).__name__}"
            ) from None
        url = f"{self._public_base_url}/{key}" if self._public_base_url else None
        logger.info(
            "r2_upload_ok key=%s bytes=%d url=%s",
            key, len(data), url or "(no public_base_url)",
        )
        return url


def make_uploader(cfg: "Config") -> R2Uploader | None:
    if not cfg.r2_enabled:
        return None
    access_key_id = os.environ.get("R2_ACCESS_KEY_ID")
    secret_access_key = os.environ.get("R2_SECRET_ACCESS_KEY")
    if not access_key_id or not secret_access_key:
        raise RuntimeError(
            "r2_enabled is true but R2_ACCESS_KEY_ID / "
            "R2_SECRET_ACCESS_KEY are not set (check .env)"
        )
    return R2Uploader(
        account_id=cfg.r2_account_id,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        bucket=cfg.r2_bucket,
        public_base_url=cfg.r2_public_base_url,
    )
