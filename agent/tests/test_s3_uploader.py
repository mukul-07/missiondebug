"""v2 P5a — S3 upload of MCAP files. Tests use a mock S3 client
(injected via S3Uploader's `client` kwarg) so no boto3 dependency is
required at test time.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from missiondebug_agent.config import S3Config
from missiondebug_agent.s3_uploader import S3Uploader


class FakeS3Client:
    """Captures the calls made to upload_file so tests can assert on them.
    Optionally raises to simulate failure."""

    def __init__(self, fail: bool = False):
        self.calls: list[dict] = []
        self.fail = fail

    def upload_file(
        self,
        Filename: str,
        Bucket: str,
        Key: str,
        ExtraArgs: dict | None = None,
    ) -> None:
        self.calls.append({
            "filename": Filename,
            "bucket": Bucket,
            "key": Key,
            "extra_args": ExtraArgs,
        })
        if self.fail:
            raise RuntimeError("simulated S3 outage")


def _cfg(**overrides) -> S3Config:
    base = dict(
        bucket="missiondebug-test",
        region="us-east-1",
        endpoint_url=None,
        public_base_url="https://missiondebug-test.s3.us-east-1.amazonaws.com",
        access_key_id="key",
        secret_access_key="secret",
        key_prefix="missiondebug/sessions",
    )
    base.update(overrides)
    return S3Config(**base)


# ---- construction guards ---------------------------------------------


def test_construct_requires_bucket():
    with pytest.raises(ValueError, match="bucket"):
        S3Uploader(S3Config(), client=FakeS3Client())


def test_construct_requires_public_base_url():
    with pytest.raises(ValueError, match="public_base_url"):
        S3Uploader(
            S3Config(bucket="b", public_base_url=None),
            client=FakeS3Client(),
        )


# ---- key + URL derivation --------------------------------------------


def test_key_for_uses_prefix_robot_session():
    u = S3Uploader(_cfg(), client=FakeS3Client())
    key = u.key_for("robot-001", "robot-001_20260513T120000Z")
    assert key == "missiondebug/sessions/robot-001/robot-001_20260513T120000Z.mcap"


def test_key_strips_trailing_slash_from_prefix():
    u = S3Uploader(_cfg(key_prefix="logs/"), client=FakeS3Client())
    assert u.key_for("r1", "s1") == "logs/r1/s1.mcap"


def test_public_url_combines_base_and_key():
    u = S3Uploader(_cfg(public_base_url="https://cdn.example.com/"), client=FakeS3Client())
    key = u.key_for("r1", "s1")
    assert u.public_url(key) == "https://cdn.example.com/missiondebug/sessions/r1/s1.mcap"


def test_public_url_with_path_in_base():
    """CDN configurations sometimes route MissionDebug to a sub-path of a
    larger bucket: base_url=https://cdn.example.com/missiondebug-prod"""
    u = S3Uploader(
        _cfg(public_base_url="https://cdn.example.com/missiondebug-prod"),
        client=FakeS3Client(),
    )
    key = u.key_for("r1", "s1")
    assert (
        u.public_url(key)
        == "https://cdn.example.com/missiondebug-prod/missiondebug/sessions/r1/s1.mcap"
    )


# ---- upload happy path -----------------------------------------------


def test_upload_happy_path(tmp_path):
    fake = FakeS3Client()
    u = S3Uploader(_cfg(), client=fake)
    mcap = tmp_path / "session.mcap"
    mcap.write_bytes(b"FAKE MCAP")

    url = u.upload(mcap, "robot-001", "sess-1")

    assert url == "https://missiondebug-test.s3.us-east-1.amazonaws.com/missiondebug/sessions/robot-001/sess-1.mcap"
    assert u.uploads_succeeded == 1
    assert u.uploads_failed == 0
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["bucket"] == "missiondebug-test"
    assert call["key"] == "missiondebug/sessions/robot-001/sess-1.mcap"
    assert call["filename"] == str(mcap)
    assert call["extra_args"] == {"ContentType": "application/octet-stream"}


# ---- upload failure --------------------------------------------------


def test_upload_failure_is_non_fatal(tmp_path):
    """Hard Rule: a broken S3 endpoint must not prevent the local save
    or the hub-side metadata report. Upload returns None; counters bump."""
    fake = FakeS3Client(fail=True)
    u = S3Uploader(_cfg(), client=fake)
    mcap = tmp_path / "session.mcap"
    mcap.write_bytes(b"FAKE MCAP")

    url = u.upload(mcap, "robot-001", "sess-1")

    assert url is None
    assert u.uploads_succeeded == 0
    assert u.uploads_failed == 1
    # Still attempted the upload.
    assert len(fake.calls) == 1


def test_upload_retry_counters_independent(tmp_path):
    """Mixed success+failure run: counters reflect each outcome."""
    fake = FakeS3Client(fail=False)
    u = S3Uploader(_cfg(), client=fake)
    mcap = tmp_path / "session.mcap"
    mcap.write_bytes(b"FAKE MCAP")

    u.upload(mcap, "r1", "s1")
    u.upload(mcap, "r1", "s2")
    fake.fail = True
    u.upload(mcap, "r1", "s3")
    fake.fail = False
    u.upload(mcap, "r1", "s4")

    assert u.uploads_succeeded == 3
    assert u.uploads_failed == 1
