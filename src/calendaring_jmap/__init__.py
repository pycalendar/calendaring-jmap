# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Calendar operations over JMAP (:rfc:`8620` + JMAP Calendars).

Provides synchronous and asynchronous JMAP clients for calendar listing,
event CRUD, incremental sync, and task CRUD.

Basic usage::

    from calendaring_jmap import get_jmap_client

    client = get_jmap_client(
        url="https://jmap.example.com/.well-known/jmap",
        username="alice",
        password="secret",
    )
    calendars = client.get_calendars()

Async usage::

    from calendaring_jmap import get_async_jmap_client

    async with get_async_jmap_client(
        url="https://jmap.example.com/.well-known/jmap",
        username="alice",
        password="secret",
    ) as client:
        calendars = await client.get_calendars()
"""

from calendaring_jmap._config import _CONN_KEYS, get_connection_params
from calendaring_jmap._version import __version__
from calendaring_jmap.async_client import AsyncJMAPClient
from calendaring_jmap.client import JMAPClient
from calendaring_jmap.error import (
    JMAPAuthError,
    JMAPCapabilityError,
    JMAPError,
    JMAPMethodError,
)
from calendaring_jmap.objects.attachment import JMAPAttachment
from calendaring_jmap.objects.busy_interval import BusyInterval
from calendaring_jmap.objects.calendar import JMAPCalendar
from calendaring_jmap.objects.calendar_object import JMAPCalendarObject


def get_jmap_client(**kwargs) -> JMAPClient | None:
    """Create a :class:`JMAPClient` from configuration.

    Configuration is read in priority order:

    1. Explicit keyword arguments (``url``, ``username``, ``password``, …)
    2. Environment variables (``JMAP_URL``, ``JMAP_USERNAME``, …)
    3. A YAML config file (``~/.config/calendaring-jmap/calendar.yaml`` or equivalent)

    Returns ``None`` if no configuration is found, rather than raising.

    Example::

        client = get_jmap_client(url="https://jmap.example.com/.well-known/jmap",
                                  username="alice", password="secret")
    """
    conn_params = get_connection_params(**kwargs)
    if conn_params is None:
        return None
    return JMAPClient(**{k: v for k, v in conn_params.items() if k in _CONN_KEYS})


def get_async_jmap_client(**kwargs) -> AsyncJMAPClient | None:
    """Create an :class:`AsyncJMAPClient` from configuration.

    Accepts the same arguments and reads configuration from the same sources
    as :func:`get_jmap_client`. Returns ``None`` if no configuration is found.

    Example::

        async with get_async_jmap_client(
            url="https://jmap.example.com/.well-known/jmap",
            username="alice", password="secret"
        ) as client:
            calendars = await client.get_calendars()
    """
    conn_params = get_connection_params(**kwargs)
    if conn_params is None:
        return None
    return AsyncJMAPClient(**{k: v for k, v in conn_params.items() if k in _CONN_KEYS})


__all__ = [
    "AsyncJMAPClient",
    "BusyInterval",
    "JMAPAttachment",
    "JMAPAuthError",
    "JMAPCalendar",
    "JMAPCalendarObject",
    "JMAPCapabilityError",
    "JMAPClient",
    "JMAPError",
    "JMAPMethodError",
    "__version__",
    "get_async_jmap_client",
    "get_jmap_client",
]
