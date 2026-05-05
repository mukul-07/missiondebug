"""Serves MCAP files with HTTP range support — critical for browser streaming."""

from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from ..db import Db

_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
_CHUNK = 1024 * 1024


def get_router(get_db) -> APIRouter:
    router = APIRouter(prefix="/api/sessions", tags=["files"])

    @router.get("/{session_id}/mcap")
    def get_mcap(session_id: str, request: Request, db: Db = Depends(get_db)):
        row = db.get_session(session_id)
        if row is None:
            raise HTTPException(404, "session not found")
        path = Path(row.mcap_path)
        if not path.exists():
            raise HTTPException(410, "mcap file missing on disk")

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

    return router


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
