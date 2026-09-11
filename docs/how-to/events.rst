.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

================
Work with events
================

Events are passed as iCalendar strings, so existing iCalendar-producing code works unchanged.

Add an event
============

.. code-block:: python

    cal = calendars[0]

    ical = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//example//EN\r\n"
        "BEGIN:VEVENT\r\n"
        "UID:meeting-2026-01-15@example.com\r\n"
        "SUMMARY:Team meeting\r\n"
        "DTSTART:20260115T100000Z\r\n"
        "DTEND:20260115T110000Z\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )

    # Returns the server-assigned JMAP event ID
    event_id = cal.add_event(ical)

Look up an event
================

By its iCalendar UID, from a :class:`~calendaring_jmap.objects.calendar.JMAPCalendar`:

.. code-block:: python

    obj = cal.get_object_by_uid("meeting-2026-01-15@example.com")

Or by its JMAP event ID, directly on the client, for example if you already have the ID from a :meth:`~calendaring_jmap.client.JMAPClient.get_objects_by_sync_token` result:

.. code-block:: python

    obj = client.get_event(event_id)

Both return a :class:`~calendaring_jmap.objects.calendar_object.JMAPCalendarObject`. Call :meth:`~calendaring_jmap.objects.calendar_object.JMAPCalendarObject.get_icalendar_instance` on it for an :class:`icalendar.Calendar`, or :meth:`~calendaring_jmap.objects.calendar_object.JMAPCalendarObject.get_data` for the raw JSCalendar dict.

Update an event
===============

Edit the object in place, then call :meth:`~calendaring_jmap.objects.calendar_object.JMAPCalendarObject.save`:

.. code-block:: python

    obj = cal.get_object_by_uid("meeting-2026-01-15@example.com")
    with obj.edit_icalendar_instance() as ical:
        ical.subcomponents[0]["SUMMARY"] = "Team standup"
    obj.save()

``save()`` needs an object with its ``parent`` calendar set, since it uses that calendar's client to send the update. Objects from ``cal.get_object_by_uid`` and :meth:`~calendaring_jmap.objects.calendar.JMAPCalendar.search` have it set; objects from :meth:`~calendaring_jmap.client.JMAPClient.get_event` don't, since they aren't fetched through a calendar. It's also sync-only; on an async-backed calendar, call :meth:`~calendaring_jmap.async_client.AsyncJMAPClient.update_event` directly instead.

If you already have a full replacement iCalendar string, ``update_event`` also works directly on the client:

.. code-block:: python

    client.update_event(event_id, updated_ical_str)

Delete an event
===============

.. code-block:: python

    client.delete_event(event_id)

Search events
=============

Use :meth:`~calendaring_jmap.objects.calendar.JMAPCalendar.search` on a calendar object:

.. code-block:: python

    cal = calendars[0]

    # All events in this calendar
    results = cal.search(event=True)

    # Time-range filter: events that overlap [start, end)
    #   start: only events ending after this datetime
    #   end:   only events starting before this datetime
    results = cal.search(
        event=True,
        start="2026-01-01T00:00:00",
        end="2026-02-01T00:00:00",
    )

    # Free-text search across title, description, locations, and participants
    results = cal.search(text="standup")

    for obj in results:
        print(obj.get_icalendar_instance())

All parameters are optional. Omitting all of them returns every event in the calendar. Results are a list of :class:`~calendaring_jmap.objects.calendar_object.JMAPCalendarObject`. The search uses a single batched JMAP request (``CalendarEvent/query`` plus a result reference into ``CalendarEvent/get``), so only one HTTP round trip is made regardless of how many events match.

Use the async client
====================

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
            cal = calendars[0]

            # Calendar-scoped methods return coroutines when the calendar
            # was obtained from an async client
            results = await cal.search(event=True)
            obj = await cal.get_object_by_uid("some-uid@example.com")
            event_id = await cal.add_event(obj.get_icalendar_instance().to_ical().decode())


    asyncio.run(main())

Event CRUD, search, sync, and task operations are all available as coroutines with identical signatures. The async client uses ``niquests.AsyncSession`` internally, so niquests is a required dependency for async use.
