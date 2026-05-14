"""v2 P5a — upload saved MCAP files to S3-compatible storage.

Closes the durability gap: today MCAPs live only on the robot's disk;
if the disk dies, the sessions explaining why die with it. P5a uploads
each saved file to a configured bucket so the hub can fetch it long
after the robot is gone.

Hard Rule 19 holds — this is opt-in. When `s3.bucket` is unset in the
agent config, this module is never imported, no boto3 dependency
required. v1.5 single-robot deployments keep working unchanged.

When configured, the flow is:
  1. mcap_writer writes /var/lib/missiondebug/sessions/<file>.mcap (local)
  2. S3Uploader uploads that file to s3://<bucket>/<key>
  3. save_now() reports mcap_url = <public_base_url>/<key> to the hub
  4. Hub stores the URL; existing /api/sessions/<id>/mcap proxy fetches
     from there on demand (no hub-side changes needed)

For production fleet deployments the customer typically fronts the
bucket with a CDN that enforces auth at the edge, or uses hub-side
presigning (v2.1+ work). v2 P5a is "the upload mechanism," not "the
production auth model."
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from .config import S3Config

log = logging.getLogger(__name__)


class S3Client(Protocol):
    """The subset of boto3's S3.Client interface we use. Defined as a
    Protocol so tests can inject a mock without depending on boto3."""

    def upload_file(
        self,
        Filename: str,
        Bucket: str,
        Key: str,
        ExtraArgs: dict | None = None,
    ) -> None: ...


class S3Uploader:
    """Wraps an S3 client + the agent's S3Config. One instance per
    agent; created at startup, called by save_now after each write."""

    def __init__(
        self,
        config: S3Config,
        *,
        client: S3Client | None = None,
    ) -> None:
        if not config.bucket:
            raise ValueError("S3Uploader requires config.bucket to be set")
        if not config.public_base_url:
            raise ValueError(
                "S3Uploader requires config.public_base_url so the hub can "
                "fetch uploaded MCAPs. For AWS, typically "
                "https://<bucket>.s3.<region>.amazonaws.com — for MinIO, "
                "set to the public HTTPS endpoint of the bucket."
            )
        self._cfg = config
        self._client: S3Client = client if client is not None else _make_default_client(config)
        # Diagnostic counters — useful for /healthz fields later.
        self.uploads_succeeded = 0
        self.uploads_failed = 0

    def key_for(self, robot_id: str, session_id: str) -> str:
        """Compute the S3 object key for a session. Used for both the
        upload destination and the public URL."""
        prefix = self._cfg.key_prefix.strip("/")
        return f"{prefix}/{robot_id}/{session_id}.mcap"

    def public_url(self, key: str) -> str:
        """The HTTPS URL the hub will use to fetch the object."""
        base = (self._cfg.public_base_url or "").rstrip("/")
        return f"{base}/{key}"

    def upload(self, local_path: Path, robot_id: str, session_id: str) -> str | None:
        """Upload the local MCAP file and return the public URL on
        success, None on failure. Failure is non-fatal — the local file
        is still saved; the agent just won't have an S3-backed URL to
        report to the hub. Caller falls back to the agent's own HTTP
        endpoint URL.
        """
        key = self.key_for(robot_id, session_id)
        try:
            self._client.upload_file(
                Filename=str(local_path),
                Bucket=self._cfg.bucket or "",  # guarded in __init__
                Key=key,
                ExtraArgs={"ContentType": "application/octet-stream"},
            )
            self.uploads_succeeded += 1
            url = self.public_url(key)
            log.info("S3 uploaded %s -> %s", session_id, url)
            return url
        except Exception as e:
            self.uploads_failed += 1
            log.warning("S3 upload failed for %s: %s", session_id, e)
            return None


def _make_default_client(cfg: S3Config) -> S3Client:
    """Lazily import boto3 only when an actual S3Uploader is being
    constructed for real. Tests inject their own client and never hit
    this path.
    """
    try:
        import boto3  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError(
            "S3 upload requires boto3. Install with: "
            "pip install missiondebug-agent[s3]"
        ) from e

    kwargs: dict = {"region_name": cfg.region}
    if cfg.endpoint_url:
        kwargs["endpoint_url"] = cfg.endpoint_url
    if cfg.access_key_id and cfg.secret_access_key:
        kwargs["aws_access_key_id"] = cfg.access_key_id
        kwargs["aws_secret_access_key"] = cfg.secret_access_key
    # When access keys aren't set, boto3 falls back to env / IAM role /
    # ~/.aws/credentials — the recommended production path.
    return boto3.client("s3", **kwargs)
