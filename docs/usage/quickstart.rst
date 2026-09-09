.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

==========
Quickstart
==========

calendaring-jmap includes a JMAP client for servers that speak :rfc:`8620` (JMAP Core) and the JMAP Calendars protocol (``urn:ietf:params:jmap:calendars``), which uses :rfc:`8984` (JSCalendar) as its data format. It covers calendar listing, event CRUD, incremental sync, and task CRUD.

.. note::

   The client targets servers implementing ``urn:ietf:params:jmap:calendars``. Cyrus IMAP is the primary tested server. Task support (``urn:ietf:params:jmap:tasks``) requires a separate server capability; Cyrus does not implement it yet.

.. code-block:: python

    from calendaring_jmap import get_jmap_client

    with get_jmap_client(
        url="https://jmap.example.com/.well-known/jmap",
        username="alice",
        password="secret",
    ) as client:
        calendars = client.get_calendars()
        for cal in calendars:
            print(cal.name)

The client keeps a persistent HTTP session, so connections are reused across requests. The ``with`` block releases it at the end. If you would rather hold on to the client, call ``client.close()`` when you are done (``await client.aclose()`` on the async client).

:func:`~calendaring_jmap.get_jmap_client` reads configuration from, in order: explicit keyword arguments, the ``JMAP_URL``/``JMAP_USERNAME``/``JMAP_PASSWORD`` environment variables, then a YAML config file. If none of those are set it returns ``None``.

With environment variables or a config file in place, no arguments are needed:

.. code-block:: python

    client = get_jmap_client()  # reads env vars or config file

Configuration
=============

Environment variables:

.. code-block:: bash

    export JMAP_URL=https://jmap.example.com/.well-known/jmap
    export JMAP_USERNAME=alice
    export JMAP_PASSWORD=secret

Or a YAML config file, at ``~/.config/calendaring-jmap/calendar.yaml`` by default, or a path passed as ``config_file``:

.. code-block:: yaml

    url: https://jmap.example.com/.well-known/jmap
    username: alice
    password: secret

Authentication
==============

HTTP Basic auth is used when a ``username`` is supplied alongside a ``password``. Bearer token auth is used when only a ``password`` (token) is given and no username. You can also pass any ``requests``-compatible auth object directly via the ``auth`` parameter (niquests is API-compatible with requests).

.. code-block:: python

    # Basic auth
    client = get_jmap_client(
        url="https://jmap.example.com/.well-known/jmap",
        username="alice",
        password="secret",
    )

    # Bearer token (password argument holds the token, no username supplied)
    client = get_jmap_client(
        url="https://jmap.example.com/.well-known/jmap",
        password="my-bearer-token",
    )

    # Pre-built auth object
    try:
        from niquests.auth import HTTPBasicAuth
    except ImportError:
        from requests.auth import HTTPBasicAuth
    client = get_jmap_client(
        url="https://jmap.example.com/.well-known/jmap",
        auth=HTTPBasicAuth("alice", "secret"),
    )

Unlike CalDAV, JMAP does not use a 401 challenge and retry. Credentials are sent on every request, and a 401 or 403 is a hard :class:`~calendaring_jmap.error.JMAPAuthError`.

Async API
=========

:class:`~calendaring_jmap.async_client.AsyncJMAPClient` mirrors every method of :class:`~calendaring_jmap.client.JMAPClient` as a coroutine. Use it as an ``async with`` context manager (sync ``with`` is not supported):

.. code-block:: python

    import asyncio

    from calendaring_jmap import get_async_jmap_client


    async def main():
        async with get_async_jmap_client(
            url="https://jmap.example.com/.well-known/jmap",
            username="alice",
            password="secret",
        ) as client:
            calendars = await client.get_calendars()
            for cal in calendars:
                print(cal.name)

            # Calendar-scoped methods return coroutines when the calendar
            # was obtained from an async client
            cal = calendars[0]
            results = await cal.search(event=True)
            ical_str = await cal.get_object_by_uid("some-uid@example.com")
            event_id = await cal.add_event(ical_str)


    asyncio.run(main())

Event CRUD, search, sync, and task operations are all available as coroutines with identical signatures. The async client uses ``niquests.AsyncSession`` internally, so niquests is a required dependency for async use.

Error handling
==============

All calendaring-jmap errors extend :class:`~calendaring_jmap.error.JMAPBaseError`, and the JMAP-specific ones extend :class:`~calendaring_jmap.error.JMAPError`, which adds an ``error_type`` string.

.. code-block:: python

    from calendaring_jmap.error import (
        JMAPAuthError,
        JMAPCapabilityError,
        JMAPError,
        JMAPMethodError,
    )

    try:
        event_id = client.create_event(calendar_id, ical)
    except JMAPAuthError:
        print("Authentication failed (401/403)")
    except JMAPCapabilityError:
        print("Server does not support urn:ietf:params:jmap:calendars")
    except JMAPMethodError as e:
        print(f"Server rejected the request: {e.error_type}, {e.reason}")
    except JMAPError as e:
        print(f"Protocol error: {e}")

The three specific error classes:

:class:`~calendaring_jmap.error.JMAPAuthError`
    HTTP 401 or 403. JMAP sends no 401 challenge, so this is always a hard
    failure.
:class:`~calendaring_jmap.error.JMAPCapabilityError`
    The server's Session object does not advertise
    ``urn:ietf:params:jmap:calendars``.
:class:`~calendaring_jmap.error.JMAPMethodError`
    A JMAP method call returned an error response. The ``error_type``
    attribute holds the :rfc:`8620` error type string, for example
    ``"invalidArguments"``, ``"notFound"``, or ``"stateMismatch"``.
