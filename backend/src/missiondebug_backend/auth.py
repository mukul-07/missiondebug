"""v2 Phase 4 — basic auth for the hub.

Two distinct credential paths:

1. **Browser users** (the engineer hitting the web UI):
   HTTP Basic Auth via the browser's native prompt. Username is
   ignored; the password must equal `MD_HUB_AUTH_PASSWORD`.

2. **Agent ingest** (robots posting sessions + heartbeats):
   `Authorization: Bearer <token>` where the token equals
   `MD_HUB_AUTH_TOKEN` (defaults to the password when unset, so a
   single-secret deployment works out of the box).

Mode (env var `MD_MODE`):
  - `single` (default): v1.5 behaviour. Auth is OPT-IN — set
    `MD_HUB_AUTH_PASSWORD` to require it; otherwise routes are open.
  - `fleet`: Hard Rule 21. Hub REFUSES TO START unless
    `MD_HUB_AUTH_PASSWORD` is set. Auth always enforced.

The `/healthz` route is always public — load balancers and container
healthchecks need it to work without credentials.
"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Callable

from fastapi import Depends, HTTPException, Request, Response

log = logging.getLogger(__name__)


class AuthConfig:
    """Reads env at construction time so it's set once per `build_app` call.

    Held as a normal class (not dataclass) because we instantiate it in
    main and pass the instance into routers as a closure.
    """

    def __init__(self) -> None:
        self.mode = os.environ.get("MD_MODE", "single").strip().lower()
        self.password = os.environ.get("MD_HUB_AUTH_PASSWORD") or None
        # Agents authenticate with a Bearer token. Default to the
        # password so a single-secret deployment works without extra
        # config; ops can rotate them independently by setting both.
        self.token = os.environ.get("MD_HUB_AUTH_TOKEN") or self.password

    @property
    def required(self) -> bool:
        """Is auth currently being enforced? Either we're in fleet mode
        (always enforced) or a password was explicitly set in single
        mode (opt-in)."""
        return self.password is not None

    def enforce_startup_invariants(self) -> None:
        """Hard Rule 21 — in fleet mode the hub refuses to start without
        a password. Raises SystemExit so docker/systemd surface a clear
        startup failure instead of running insecure."""
        if self.mode == "fleet" and not self.password:
            log.error(
                "Refusing to start: MD_MODE=fleet but MD_HUB_AUTH_PASSWORD "
                "is not set. Hard Rule 21 (v2-SPEC) requires auth in "
                "fleet mode. Set MD_HUB_AUTH_PASSWORD or run with "
                "MD_MODE=single (single-robot v1.5 behaviour, no auth).",
            )
            raise SystemExit(2)
        if self.required:
            log.info(
                "Auth enabled (mode=%s). Browser users: HTTP Basic. "
                "Agents: Authorization: Bearer <token>.",
                self.mode,
            )
        else:
            log.info(
                "Auth disabled (mode=%s, no MD_HUB_AUTH_PASSWORD set). "
                "v1.5 network-trust deployment.",
                self.mode,
            )


_UNAUTHORIZED_HEADERS = {"WWW-Authenticate": 'Basic realm="MissionDebug"'}


def _check_basic(authorization: str | None, expected_password: str) -> bool:
    """Returns True iff the header is `Basic <b64>` and decoded password
    matches. Username is ignored — single-password deployment."""
    if not authorization or not authorization.lower().startswith("basic "):
        return False
    try:
        import base64

        token = authorization.split(" ", 1)[1].strip()
        decoded = base64.b64decode(token).decode("utf-8", errors="replace")
        if ":" not in decoded:
            return False
        _user, password = decoded.split(":", 1)
        return secrets.compare_digest(password, expected_password)
    except Exception:
        return False


def _check_bearer(authorization: str | None, expected_token: str) -> bool:
    if not authorization or not authorization.lower().startswith("bearer "):
        return False
    presented = authorization.split(" ", 1)[1].strip()
    return secrets.compare_digest(presented, expected_token)


def make_auth_dependency(cfg: AuthConfig) -> Callable[[Request], None]:
    """Factory returning a FastAPI dependency that enforces auth on the
    routes it's attached to. When auth is disabled, the dependency is a
    no-op so v1.5 callers see no behaviour change."""

    def dependency(request: Request) -> None:
        if not cfg.required:
            return
        auth = request.headers.get("authorization")
        if cfg.password and _check_basic(auth, cfg.password):
            return
        if cfg.token and _check_bearer(auth, cfg.token):
            return
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers=_UNAUTHORIZED_HEADERS,
        )

    return dependency


def is_public_path(path: str) -> bool:
    """Paths exempt from auth regardless of mode. /healthz only for
    now — load balancers + container healthchecks need it."""
    return path == "/healthz" or path == "/"
