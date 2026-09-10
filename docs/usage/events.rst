.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

======
Events
======

Events are passed as iCalendar strings, so existing iCalendar-producing code works unchanged.

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

    # Add an event to this calendar, returns the server-assigned JMAP event ID
    event_id = cal.add_event(ical)

    # Look up an event by its iCalendar UID, returns a VCALENDAR string
    ical_str = cal.get_object_by_uid("meeting-2026-01-15@example.com")

If you already have a JMAP event ID, for example from :meth:`~calendaring_jmap.client.JMAPClient.get_sync_token` results, you can also use the lower-level client methods directly:

.. code-block:: python

    # Fetch by JMAP event ID, returns a VCALENDAR string
    ical_str = client.get_event(event_id)

    # Update: pass a complete VCALENDAR string with the changes applied
    updated = ical_str.replace("Team meeting", "Team standup")
    client.update_event(event_id, updated)

    # Delete
    client.delete_event(event_id)

Searching events
================

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

    for ical_str in results:
        print(ical_str)

All parameters are optional. Omitting all of them returns every event in the calendar. Results are returned as a list of VCALENDAR strings. The search uses a single batched JMAP request (``CalendarEvent/query`` plus a result reference into ``CalendarEvent/get``), so only one HTTP round trip is made regardless of how many events match.
