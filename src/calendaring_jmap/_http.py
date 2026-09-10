# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""HTTP library selection, imported once for the whole package.

Everything that needs niquests-or-requests takes it from here rather than
repeating the fallback. ``requests`` is niquests when niquests is installed:
the name is the one the API is compatible with, not necessarily the package
that provides it.
"""

from typing import Any

_DOCS_URL = "https://calendaring-jmap.readthedocs.io/"

USE_NIQUESTS = False
USE_REQUESTS = False

## niquests' AsyncSession has no requests equivalent, so it's None on the
## fallback. The async client is the only thing that needs it; it goes
## through require_async_session() for a clear error instead of a
## TypeError on None.
AsyncSession: Any = None

try:
    import niquests as requests
    from niquests.auth import AuthBase, HTTPBasicAuth

    USE_NIQUESTS = True
except ImportError:
    try:
        import requests  # type: ignore[no-redef]
        from requests.auth import (  # type: ignore[assignment]
            AuthBase,
            HTTPBasicAuth,
        )

        USE_REQUESTS = True
    except ImportError as e:
        raise ImportError(
            "calendaring-jmap needs an HTTP library, and none of the supported "
            "ones is installed (tried: niquests, requests). Install one; "
            "`pip install niquests` is the recommended choice. "
            f"See {_DOCS_URL} for details."
        ) from e

if USE_NIQUESTS:
    ## Deliberately its own try: an ImportError here must not fall through to
    ## the requests branch and flip USE_NIQUESTS off on an install that does
    ## have niquests. Only the async client needs this.
    try:
        from niquests import AsyncSession
    except ImportError:
        ## Old niquests without AsyncSession: leave it None, and let
        ## require_async_session() explain it if anything asks for it.
        pass


def require_async_session() -> Any:
    """Return niquests' ``AsyncSession``, or explain why there isn't one.

    The async client is built on it and has no fallback.
    """
    if AsyncSession is None:
        reason = "an old niquests install without AsyncSession" if USE_NIQUESTS else "not installed"
        raise ImportError(
            f"The async JMAP client requires niquests with AsyncSession support ({reason}). "
            "Unlike the sync client it has no fallback to another HTTP library. "
            f"Install or upgrade it with `pip install -U niquests`. See {_DOCS_URL} for details."
        )
    return AsyncSession


class HTTPBearerAuth(AuthBase):
    """Bearer token authentication for niquests/requests."""

    def __init__(self, password: str) -> None:
        self.password = password

    def __eq__(self, other: object) -> bool:
        return self.password == getattr(other, "password", None)

    def __ne__(self, other: object) -> bool:
        return not self == other

    def __call__(self, r):
        r.headers["Authorization"] = f"Bearer {self.password}"
        return r


__all__ = [
    "AsyncSession",
    "AuthBase",
    "HTTPBasicAuth",
    "HTTPBearerAuth",
    "USE_NIQUESTS",
    "USE_REQUESTS",
    "requests",
    "require_async_session",
]
