# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""JMAP error hierarchy.

:rfc:`8620#section-3.6.2` defines the standard method-level error types.

If ``caldav`` happens to be installed alongside this package, JMAPError and
JMAPAuthError subclass its DAVError/AuthorizationError too, so code that
catches ``caldav.lib.error.DAVError`` around CalDAV calls also catches JMAP
errors raised here. There is no hard dependency on caldav, just an optional
compatibility hook when it's present.
"""

from __future__ import annotations

#: Fallback ``error_type`` used when a server's error response omits its
#: own ``"type"`` field. Not itself a real :rfc:`8620#section-3.6.2` error type, just
#: this package's own default for a nonconformant response.
_DEFAULT_ERROR_TYPE = "serverFail"

try:
    # Both branches define _CaldavDAVError/_CaldavAuthorizationError; only
    # one ever runs, but static checkers see it as one name defined twice
    # and flag the second definition as incompatible with the first. mypy
    # calls this `no-redef`; pyright/Pylance calls it `reportAssignmentType`.
    # Both comments are needed since the two tools use different codes for
    # the same complaint about the same lines.
    from caldav.lib.error import (
        AuthorizationError as _CaldavAuthorizationError,  # type: ignore[assignment]  # pyright: ignore[reportAssignmentType]
    )
    from caldav.lib.error import (
        DAVError as _CaldavDAVError,  # type: ignore[assignment]  # pyright: ignore[reportAssignmentType]
    )
except ImportError:

    class _CaldavDAVError(Exception):  # type: ignore[no-redef]
        pass

    class _CaldavAuthorizationError(_CaldavDAVError):  # type: ignore[no-redef]
        pass


class JMAPBaseError(_CaldavDAVError):
    """Base for all calendaring-jmap errors: a URL, a reason, nothing more."""

    url: str | None = None
    reason: str = "no reason"

    def __init__(self, url: str | None = None, reason: str | None = None) -> None:
        if url:
            self.url = url
        if reason:
            self.reason = reason

    def __str__(self) -> str:
        return f"{self.__class__.__name__} at '{self.url}', reason {self.reason}"


class JMAPError(JMAPBaseError):
    """Base class for all JMAP protocol errors.

    Adds ``error_type`` to carry the :rfc:`8620` error type string
    (e.g. ``"unknownMethod"``, ``"invalidArguments"``).
    """

    error_type: str = _DEFAULT_ERROR_TYPE

    def __init__(
        self,
        url: str | None = None,
        reason: str | None = None,
        error_type: str | None = None,
    ) -> None:
        super().__init__(url=url, reason=reason)
        if error_type is not None:
            self.error_type = error_type

    def __str__(self) -> str:
        return (
            f"{self.__class__.__name__} (type={self.error_type}) "
            f"at '{self.url}', reason: {self.reason}"
        )


class JMAPCapabilityError(JMAPError):
    """Server does not advertise the required JMAP capability.

    Raised when the Session object returned by the server does not include
    ``urn:ietf:params:jmap:calendars`` in the account capabilities.
    """

    error_type = "capabilityNotSupported"
    reason = "Server does not support urn:ietf:params:jmap:calendars"


class JMAPAuthError(_CaldavAuthorizationError, JMAPError):
    """HTTP 401 or 403 received from a JMAP server.

    Unlike CalDAV, JMAP does not use a 401-challenge-retry dance.
    A 401/403 on the session GET or any API call is a hard failure.
    """

    error_type = "forbidden"
    reason = "Authentication failed"


class JMAPMethodError(JMAPError):
    """A JMAP method call returned an error response.

    :rfc:`8620#section-3.6.2` generic error types that may be set as
    ``error_type``:

    - ``serverUnavailable``: temporary, retrying later may succeed
    - ``serverFail``: unexpected server-side error (default here, used
      when the server's response omits ``type`` entirely)
    - ``serverPartialFail``: partial failure, some calls succeeded
    - ``unknownMethod``: method name not recognised
    - ``invalidArguments``: bad argument types or values
    - ``invalidResultReference``: bad ``#result`` reference
    - ``forbidden``: not allowed to perform this call
    - ``accountNotFound``: ``accountId`` does not exist
    - ``accountNotSupportedByMethod``: account lacks needed capability
    - ``accountReadOnly``: account is read-only

    Individual methods define further, method-specific error types beyond
    this generic set (e.g. ``notFound``, ``calendarHasEvent``,
    ``expandDurationTooLarge``); see each method builder's own docstring.
    """

    error_type = _DEFAULT_ERROR_TYPE
