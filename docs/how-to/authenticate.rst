.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

============
Authenticate
============

:func:`~calendaring_jmap.get_jmap_client` reads configuration from, in order: explicit keyword arguments, environment variables, then a YAML config file. If none of those are set, it returns ``None``.

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

HTTP Basic auth is used when a ``username`` is supplied alongside a ``password``. Bearer token auth is used when only a ``password`` (token) is given and no username.

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

Unlike CalDAV, JMAP does not use a 401 challenge and retry. Credentials are sent on every request, and a 401 or 403 is a hard :class:`~calendaring_jmap.error.JMAPAuthError`.
