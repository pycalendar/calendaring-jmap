.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

============
Authenticate
============

:func:`~calendaring_jmap.get_jmap_client` reads configuration from explicit keyword arguments, environment variables, then a YAML config file, checked in that order. If none of the three sources supplies a ``url``, it returns ``None``.

The first source that supplies a ``url`` wins outright for that call: passing ``url`` explicitly, even with no other arguments, uses only the explicit arguments, and doesn't fall back to environment variables or the config file for ``username``/``password``/``auth_type``/``timeout``. If you mix sources, supply ``url`` from the lowest-priority source you want to use; a source that doesn't supply ``url`` still has its other fields merged in underneath whichever source does.

Resolve configuration without building a client
===============================================

:func:`~calendaring_jmap.get_connection_params` runs the same resolution :func:`~calendaring_jmap.get_jmap_client` does, but returns the resolved dict instead of a client, or ``None`` if nothing was found. Useful if you want to inspect or adjust the result before constructing a client yourself, or you're building your own thin wrapper around this project's config sources (for example, a CLI tool that passes every flag through unconditionally, whether or not the user set it, without wiping out an environment variable for the flags they left off):

.. code-block:: python

    from calendaring_jmap import get_connection_params

    params = get_connection_params(url=cli_args.url, username=cli_args.username)
    if params is None:
        raise SystemExit("No JMAP configuration found")

Use environment variables or a config file
==========================================

Environment variables:

.. code-block:: bash

    export JMAP_URL=https://jmap.example.com/.well-known/jmap
    export JMAP_USERNAME=alice
    export JMAP_PASSWORD=secret
    export JMAP_AUTH_TYPE=basic
    export JMAP_TIMEOUT=30

``JMAP_URL``, ``JMAP_USERNAME``, and ``JMAP_PASSWORD`` map directly to :func:`~calendaring_jmap.get_jmap_client`'s own ``url``/``username``/``password`` arguments. ``JMAP_AUTH_TYPE`` (``basic`` or ``bearer``) forces the auth type instead of inferring it; ``JMAP_TIMEOUT`` sets the request timeout in seconds. Both are optional.

Or a YAML config file, at ``~/.config/calendaring-jmap/calendar.yaml`` by default:

.. code-block:: yaml

    url: https://jmap.example.com/.well-known/jmap
    username: alice
    password: secret

Override the config file's location with the ``JMAP_CONFIG_FILE`` environment variable, or by passing ``config_file`` directly to :func:`~calendaring_jmap.get_jmap_client`.

With either in place, no arguments are needed:

.. code-block:: python

    client = get_jmap_client()

Use Basic or Bearer auth explicitly
===================================

Supplying a ``username`` alongside a ``password`` uses HTTP Basic auth. Supplying only a ``password`` (token), with no username, uses Bearer token auth.

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

Use a pre-built auth object
===========================

Pass any ``requests``-compatible auth object directly via the ``auth`` parameter (niquests is API-compatible with requests):

.. code-block:: python

    try:
        from niquests.auth import HTTPBasicAuth
    except ImportError:
        from requests.auth import HTTPBasicAuth
    client = get_jmap_client(
        url="https://jmap.example.com/.well-known/jmap",
        auth=HTTPBasicAuth("alice", "secret"),
    )

JMAP doesn't use a 401 challenge and retry. Credentials are sent on every request, and a 401 or 403 is a hard :class:`~calendaring_jmap.error.JMAPAuthError`.

Release the connection without a context manager
================================================

If you don't use ``with``/``async with``, call :meth:`~calendaring_jmap.client.JMAPClient.close` (or :meth:`~calendaring_jmap.async_client.AsyncJMAPClient.aclose` on the async client) when you're done, to release the underlying HTTP session and its connection pool:

.. code-block:: python

    client = get_jmap_client()
    try:
        calendars = client.get_calendars()
    finally:
        client.close()

Calling it is optional. Forgetting isn't harmful: the session is recreated automatically on the next request.
