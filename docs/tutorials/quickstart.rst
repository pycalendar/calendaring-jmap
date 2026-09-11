.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

==========
Quickstart
==========

This tutorial walks you through listing your calendars with calendaring-jmap. It assumes you have access to a JMAP server that speaks :rfc:`8620` and the JMAP Calendars protocol (``urn:ietf:params:jmap:calendars``); Cyrus IMAP is the server calendaring-jmap is tested against most.

Install the package
===================

.. code-block:: bash

    pip install calendaring-jmap

Not yet published to PyPI. See :doc:`../how-to/install` for installing from source.

Create a client
===============

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

Replace the URL, username, and password with your own server's details, then run the script. You should see the names of your calendars printed.

What happened
=============

``get_jmap_client`` created a :class:`~calendaring_jmap.client.JMAPClient`, which fetched the server's JMAP Session object and used it to call :meth:`~calendaring_jmap.client.JMAPClient.get_calendars`. The ``with`` block keeps the underlying HTTP connection open across calls and closes it when you're done.

Next steps
==========

- :doc:`../how-to/authenticate` for other ways to supply credentials.
- :doc:`../how-to/events` to create, read, update, and delete events.
- :doc:`../how-to/errors` to handle what can go wrong.
- :doc:`../explanation/design` to understand how the client is put together, and why.
