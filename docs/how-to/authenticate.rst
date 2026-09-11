.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

============
Authenticate
============

:func:`~calendaring_jmap.get_jmap_client` reads configuration from, in order: explicit keyword arguments, the ``JMAP_URL``/``JMAP_USERNAME``/``JMAP_PASSWORD`` environment variables, then a YAML config file. If none of those are set, it returns ``None``.

Use environment variables or a config file
==========================================

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
