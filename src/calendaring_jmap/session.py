# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""JMAP session establishment (:rfc:`8620#section-2`).

Fetches the Session object from /.well-known/jmap and extracts the
information needed to make subsequent API calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote, urljoin, urlparse, urlunparse

from calendaring_jmap._http import AsyncSession, requests
from calendaring_jmap.constants import CALENDAR_CAPABILITY
from calendaring_jmap.error import JMAPAuthError, JMAPCapabilityError

_URI_TEMPLATE_VAR = re.compile(r"\{(\w+)\}")


@dataclass
class Session:
    """Parsed JMAP Session object (:rfc:`8620#section-2`).

    Attributes:
        api_url: URL to POST method calls to.
        account_id: The accountId to use for calendar method calls.
            Chosen from ``primaryAccounts`` if available, otherwise the first
            account advertising the calendars capability.
        state: Current session state string.
        account_capabilities: Capabilities dict for the chosen account.
        server_capabilities: Server-level capabilities dict.
        upload_url: URI Template (:rfc:`8620#section-6.1`) for uploading a
            blob, or ``None`` if the server omits it.
        download_url: URI Template (:rfc:`8620#section-6.2`) for downloading
            a blob, or ``None`` if the server omits it.
        raw: The full parsed Session JSON for anything not captured above.
    """

    api_url: str
    account_id: str
    state: str
    account_capabilities: dict = field(default_factory=dict)
    server_capabilities: dict = field(default_factory=dict)
    upload_url: str | None = None
    download_url: str | None = None
    raw: dict = field(default_factory=dict)


def _resolve_session_url(session_url: str, url: str) -> str:
    """Resolve a URL (or URI Template) from the Session object against the
    session endpoint's own URL.

    Shared by ``apiUrl``, ``uploadUrl``, and ``downloadUrl``: RFC 8620 §2
    says each SHOULD be absolute, but some servers (e.g. Cyrus) return a
    relative path, and some (e.g. Stalwart) return an absolute URL whose
    host matches but whose scheme/port doesn't match the one actually
    connected through. ``urljoin``/``urlparse`` treat ``{``/``}`` as opaque
    characters, so this is safe to use on a URI Template, not just a plain
    URL.
    """
    resolved = urljoin(session_url, url)
    session_parsed = urlparse(session_url)
    resolved_parsed = urlparse(resolved)
    if resolved_parsed.hostname == session_parsed.hostname and (
        resolved_parsed.port != session_parsed.port
        or resolved_parsed.scheme != session_parsed.scheme
    ):
        resolved = urlunparse(
            resolved_parsed._replace(scheme=session_parsed.scheme, netloc=session_parsed.netloc)
        )
    return resolved


def _parse_session_data(url: str, data: dict) -> Session:
    api_url = data.get("apiUrl")
    if not api_url:
        raise JMAPCapabilityError(
            url=url,
            reason="Session response missing 'apiUrl'",
        )
    api_url = _resolve_session_url(url, api_url)

    ## "" and absent both become None, matching apiUrl's own check above;
    ## _require_blob_url (client.py) only checks "is None".
    upload_url = data.get("uploadUrl") or None
    if upload_url:
        upload_url = _resolve_session_url(url, upload_url)
    download_url = data.get("downloadUrl") or None
    if download_url:
        download_url = _resolve_session_url(url, download_url)

    state = data.get("state", "")
    server_capabilities = data.get("capabilities", {})
    accounts = data.get("accounts", {})

    account_id = None
    account_capabilities: dict = {}
    primary_acct_id = data.get("primaryAccounts", {}).get(CALENDAR_CAPABILITY)
    if primary_acct_id:
        acct_data = accounts.get(primary_acct_id, {})
        caps = acct_data.get("accountCapabilities", {})
        if CALENDAR_CAPABILITY in caps:
            account_id = primary_acct_id
            account_capabilities = caps
    if account_id is None:
        for acct_id, acct_data in accounts.items():
            caps = acct_data.get("accountCapabilities", {})
            if CALENDAR_CAPABILITY in caps:
                account_id = acct_id
                account_capabilities = caps
                break

    if account_id is None:
        raise JMAPCapabilityError(
            url=url,
            reason=(
                f"No account found with capability {CALENDAR_CAPABILITY!r}. "
                f"Available accounts: {list(accounts.keys())}"
            ),
        )

    return Session(
        api_url=api_url,
        account_id=account_id,
        state=state,
        account_capabilities=account_capabilities,
        server_capabilities=server_capabilities,
        upload_url=upload_url,
        download_url=download_url,
        raw=data,
    )


def _expand_uri_template(template: str, variables: dict[str, str]) -> str:
    """Expand an RFC 6570 Level 1 URI Template.

    Used for the Session object's ``uploadUrl``/``downloadUrl`` (RFC 8620
    section 6.1/6.2), both of which are Level 1 templates: each ``{var}``
    is replaced by the corresponding value from ``variables``, percent-encoded
    with every character outside ALPHA/DIGIT/-._~ escaped.

    Args:
        template: A URI Template string, e.g. ``"/upload/{accountId}/"``.
        variables: Map of template variable name to its literal (unencoded)
            value. A variable referenced in ``template`` but absent here is
            left unexpanded.

    Returns:
        The expanded URI.
    """
    return _URI_TEMPLATE_VAR.sub(
        lambda m: quote(variables[m.group(1)], safe="") if m.group(1) in variables else m.group(0),
        template,
    )


def fetch_session(url: str, auth, timeout: int = 30) -> Session:
    """Fetch and parse the JMAP Session object.

    Performs a GET request to ``url`` (expected to be ``/.well-known/jmap``
    or equivalent), authenticates with ``auth``, and returns a parsed
    :class:`Session`.

    Args:
        url: Full URL to the JMAP session endpoint.
        auth: A requests-compatible auth object (e.g. HTTPBasicAuth,
              HTTPBearerAuth).

    Returns:
        Parsed :class:`Session` with ``api_url`` and ``account_id`` set.

    Raises:
        JMAPAuthError: If the server returns HTTP 401 or 403.
        JMAPCapabilityError: If no account advertises the calendars capability.
        requests.HTTPError: For other non-2xx responses.
    """
    response = requests.get(url, auth=auth, headers={"Accept": "application/json"}, timeout=timeout)
    if response.status_code in (401, 403):
        raise JMAPAuthError(url=url, reason=f"HTTP {response.status_code} from session endpoint")
    response.raise_for_status()
    return _parse_session_data(url, response.json())


async def async_fetch_session(url: str, auth, timeout: int = 30) -> Session:
    """Async variant of :func:`fetch_session` using niquests.AsyncSession.

    Args:
        url: Full URL to the JMAP session endpoint.
        auth: A niquests-compatible auth object.

    Returns:
        Parsed :class:`Session` with ``api_url`` and ``account_id`` set.

    Raises:
        JMAPAuthError: If the server returns HTTP 401 or 403.
        JMAPCapabilityError: If no account advertises the calendars capability.
    """
    async with AsyncSession() as session:
        response = await session.get(
            url, auth=auth, headers={"Accept": "application/json"}, timeout=timeout
        )
    if response.status_code in (401, 403):
        raise JMAPAuthError(url=url, reason=f"HTTP {response.status_code} from session endpoint")
    response.raise_for_status()
    return _parse_session_data(url, response.json())
