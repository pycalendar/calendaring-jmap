.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

===================
Work with calendars
===================

List calendars
==============

.. code-block:: python

    calendars = client.get_calendars()
    for cal in calendars:
        print(cal.id, cal.name, cal.color)

Each item is a :class:`~calendaring_jmap.objects.calendar.JMAPCalendar`; see :doc:`../reference/objects` for its fields.

Pass ``account_id`` to list calendars on another account that's been shared with you:

.. code-block:: python

    calendars = client.get_calendars(account_id=other_account_id)

Create a calendar
=================

.. code-block:: python

    calendar_id = client.create_calendar(name="Team events", color="#2E86AB")

``name`` is required. ``color`` and ``timezone`` are optional.

Update a calendar
=================

.. code-block:: python

    client.update_calendar(calendar_id, name="Team events (archived)")

Only the arguments you pass change; the rest are left alone. ``name``, ``color``, and ``timeZone`` are per-user: updating them as the calendar's owner changes what every sharee sees, until a sharee sets their own override on top.

Delete a calendar
=================

.. code-block:: python

    client.delete_calendar(calendar_id)

Deleting a calendar that still has events raises :class:`~calendaring_jmap.error.JMAPMethodError` with ``error_type`` ``"calendarHasEvent"``, rather than deleting anything, unless you pass ``on_destroy_remove_events=True``:

.. code-block:: python

    client.delete_calendar(calendar_id, on_destroy_remove_events=True)

Share a calendar
================

.. code-block:: python

    client.share_calendar(
        calendar_id,
        account_id=principal_id,
        rights={"mayReadItems": True, "mayWriteItems": True},
    )

``account_id`` must already be a resolved JMAP Principal ID, not an email address; this client has no ``Principal/query`` support yet, so resolving an email to a Principal ID is left to you. ``rights`` replaces that account's entire rights entry on this calendar; it isn't merged with rights the account already had, so granting one additional right still needs the full rights dict. Sharing requires you to already hold the ``mayShare`` right yourself, and you can't grant a right you don't hold.

Read back a calendar's current sharing configuration from its ``share_with`` field, populated when you have the ``mayShare`` right yourself and ``None`` otherwise:

.. code-block:: python

    cal = client.get_calendars()[0]
    print(cal.share_with)
    # {"principal-abc": {"mayReadItems": True, "mayWriteItems": False}}

Set default alerts
==================

New events created on a calendar can inherit default alerts, one set for timed events and one for all-day events:

.. code-block:: python

    client.set_default_alerts(
        calendar_id,
        alerts_with_time={"a1": {"@type": "Alert", "trigger": {"@type": "OffsetTrigger", "offset": "-PT15M"}}},
        alerts_without_time={},
    )

Pass ``None`` (the default) for either argument to leave it unchanged; pass ``{}`` to clear it.

Subscriptions
=============

.. code-block:: python

    subscribed = client.get_calendar_subscriptions()

Equivalent to filtering :meth:`~calendaring_jmap.client.JMAPClient.get_calendars` to ``is_subscribed``; JMAP Calendars has no separate subscription object.
