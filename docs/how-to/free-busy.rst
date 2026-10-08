.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

===============
Check free/busy
===============

.. code-block:: python

    availability = client.get_availability(
        account_ids=[account_id],
        start="2026-01-15T00:00:00",
        end="2026-01-16T00:00:00",
    )
    for interval in availability[account_id]:
        print(interval.start, interval.end, interval.busy_status)

Each result is a list of :class:`~calendaring_jmap.objects.busy_interval.BusyInterval` objects. ``start`` and ``end`` are treated as UTC.

Only reports your own availability, never another user's: checking someone else's by email would need resolving that email to a Principal ID first, which this client doesn't support yet. Pass your own account's ID as the only entry in ``account_ids``.

Request event details
=====================

.. code-block:: python

    availability = client.get_availability(
        account_ids=[account_id],
        start="2026-01-15T00:00:00",
        end="2026-01-16T00:00:00",
        show_details=True,
    )

Populates each interval's ``event`` where the server permits it. Stalwart needs the account to support returning event details at all; without that, ``event`` comes back ``None`` even with ``show_details=True``. Cyrus returns details by default.

If the server doesn't support the primary method for a given account, :meth:`~calendaring_jmap.client.JMAPClient.get_availability` falls back to scanning that account's own calendar events directly and computing busy intervals from their ``freeBusyStatus``. This happens automatically; no separate call is needed.
