"""Serves MCAP files with HTTP range support — critical for browser streaming.

Two backends:

- Local file (v1.5 single-robot, also used when the hub *is* the agent):
  the row's `mcap_path` is a path on this server's disk, opened directly.

- HTTP proxy to a remote agent (v2 fleet, P1.5):
  the row's `mcap_url` is set to an HTTP URL pointing at an agent's
  /api/sessions/{id}/mcap. The hub streams the response through to the
  browser, forwarding the Range header so seeks remain cheap. The MCAP
  bytes never get persisted on the hub.

Selection rule: if `mcap_url` is set and looks like http(s), we proxy.
Otherwise we fall back to the local file path. S3-style URLs are
recognised but return 501 until v2 Phase 5 lands.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from ..db import Db

_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
_CHUNK = 1024 * 1024

# Reasonable timeouts for proxying a single robot on the LAN. Read is
# generous because MCAP files can be tens of MB.
_PROXY_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)


def get_router(get_db) -> APIRouter:
    router = APIRouter(prefix="/api/sessions", tags=["files"])

    @router.api_route(
        "/{session_id}/recording.mcap",
        methods=["GET", "HEAD"],
        summary="Stream the session's MCAP file, .mcap-suffixed URL",
    )
    @router.api_route(
        "/{session_id}/mcap",
        methods=["GET", "HEAD"],
        summary="Stream the session's MCAP file (supports Range)",
    )
    async def get_mcap(session_id: str, request: Request, db: Db = Depends(get_db)):
        """Returns the raw MCAP bytes. Honors HTTP Range requests so the
        browser scrubber can fetch arbitrary chunks for fast seek.

        When the session has an HTTP `mcap_url` (hub-ingested, v2 fleet)
        the request is proxied to that URL — bytes are streamed through,
        not buffered on the hub.

        Served at TWO paths: `/mcap` (the original, used by the hub's own
        replay worker) and `/recording.mcap`. The alias exists because
        Foxglove's remote-file data source infers the format from the URL
        path's file extension — a URL ending in `/mcap` has none, and
        Foxglove rejects it with "Unsupported extension". Deep links (the
        hub UI's Open-in-Foxglove button, the Foxglove panel's Open here)
        must use the `.mcap`-suffixed path.
        """
        row = db.get_session(session_id)
        if row is None:
            raise HTTPException(404, "session not found")

        # v2: proxy to the remote agent if mcap_url is set and HTTP(S).
        if row.mcap_url:
            scheme = urlparse(row.mcap_url).scheme
            if scheme in ("http", "https"):
                return await _proxy_get(row.mcap_url, request)
            if scheme in ("s3",):
                # Phase 5 will resolve this to a presigned URL.
                raise HTTPException(
                    501, f"mcap_url scheme {scheme!r} not yet supported in v2 P1"
                )
            raise HTTPException(500, f"unknown mcap_url scheme {scheme!r}")

        # v1.5 path: local file.
        if not row.mcap_path:
            raise HTTPException(410, "no mcap_path on this session")
        path = Path(row.mcap_path)
        if not path.exists():
            raise HTTPException(410, "mcap file missing on disk")
        return _serve_local(path, request)

    return router


# ---- local file path (v1.5) -------------------------------------------


def _serve_local(path: Path, request: Request):
    size = path.stat().st_size
    range_header = request.headers.get("range")

    if range_header is None:
        return _full_response(path, size)

    m = _RANGE_RE.match(range_header)
    if not m:
        raise HTTPException(416, "invalid Range header")
    start_s, end_s = m.groups()
    if start_s == "":
        # suffix range: bytes=-N -> last N bytes
        length = int(end_s)
        start = max(0, size - length)
        end = size - 1
    else:
        start = int(start_s)
        end = int(end_s) if end_s else size - 1

    if start >= size or end >= size or start > end:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{size}"},
        )

    length = end - start + 1
    headers = {
        "Content-Range": f"bytes {start}-{end}/{size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Content-Type": "application/octet-stream",
    }
    return StreamingResponse(
        _file_iter(path, start, length),
        status_code=206,
        headers=headers,
    )


def _full_response(path: Path, size: int) -> StreamingResponse:
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(size),
        "Content-Type": "application/octet-stream",
    }
    return StreamingResponse(
        _file_iter(path, 0, size), status_code=200, headers=headers
    )


def _file_iter(path: Path, start: int, length: int):
    with open(path, "rb") as f:
        f.seek(start)
        remaining = length
        while remaining > 0:
            chunk = f.read(min(_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


# ---- proxy to a remote agent (v2 fleet) -------------------------------


# Headers worth forwarding from agent -> client. Hop-by-hop headers
# (connection, transfer-encoding) and rewritten ones (content-length
# is set automatically by Starlette) are excluded.
_FORWARD_RESPONSE_HEADERS = {
    "content-range",
    "content-length",
    "accept-ranges",
    "content-type",
    "etag",
    "last-modified",
}


async def _proxy_get(upstream_url: str, request: Request):
    """Stream-proxy GET against upstream_url, forwarding the Range header
    if present and the relevant response headers. The hub does not buffer
    the body — it streams chunks straight through."""
    headers: dict[str, str] = {}
    if "range" in request.headers:
        headers["range"] = request.headers["range"]

    client = httpx.AsyncClient(timeout=_PROXY_TIMEOUT)
    try:
        # Build the stream as a context manager so we can pass it to
        # StreamingResponse and close the client when the body is drained.
        req = client.build_request("GET", upstream_url, headers=headers)
        resp = await client.send(req, stream=True)
    except httpx.HTTPError as e:
        await client.aclose()
        raise HTTPException(502, f"upstream agent unreachable: {e}") from e

    if resp.status_code >= 500:
        await resp.aclose()
        await client.aclose()
        raise HTTPException(502, f"upstream agent returned {resp.status_code}")
    if resp.status_code == 416:
        await resp.aclose()
        await client.aclose()
        raise HTTPException(416, "range not satisfiable")
    if resp.status_code == 404:
        await resp.aclose()
        await client.aclose()
        raise HTTPException(410, "mcap not found on upstream agent")

    out_headers = {
        k: v
        for k, v in resp.headers.items()
        if k.lower() in _FORWARD_RESPONSE_HEADERS
    }

    async def body_iter():
        try:
            async for chunk in resp.aiter_bytes(_CHUNK):
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(
        body_iter(),
        status_code=resp.status_code,
        headers=out_headers,
    )
