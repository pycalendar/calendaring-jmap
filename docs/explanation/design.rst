.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

======
Design
======

Why JMAP identity is opaque
===========================

JMAP identifies calendars, events, and tasks by opaque server-assigned IDs, not URLs. This is a real difference from CalDAV, which is URL-addressed: a CalDAV client's identity for an object is the URL it lives at, and moving or renaming a resource can change that URL. A JMAP object's ID stays the same for its lifetime regardless of where the server chooses to store it.

This shapes the client's API: :meth:`~calendaring_jmap.client.JMAPClient.create_event` and similar methods return an ID string, not a URL, and every subsequent lookup, update, or delete uses that ID. There's no separate "fetch the URL, then act on it" step.

Why there's a sync and an async client
======================================

:class:`~calendaring_jmap.async_client.AsyncJMAPClient` mirrors every public method of :class:`~calendaring_jmap.client.JMAPClient` as a coroutine, rather than the library offering only one and asking callers to wrap it. Both share their parsing and request-building logic through a common base class (``_JMAPClientBase``), so the two clients don't duplicate that logic, only the transport call itself differs (blocking request vs. ``await``).

:class:`~calendaring_jmap.objects.calendar.JMAPCalendar` and :class:`~calendaring_jmap.objects.calendar_object.JMAPCalendarObject` need to support both: the same calendar object type is returned whether it came from the sync or the async client, and its methods (``search``, ``add_event``, and similar) need to return a plain value in the sync case and a coroutine in the async case. Python's type system can't narrow a return type based on a runtime flag, so calendaring-jmap uses a generic type parameter on ``JMAPCalendar`` purely for static typing: it lets type checkers give a correctly-typed result depending on whether the calendar came from :meth:`~calendaring_jmap.client.JMAPClient.get_calendars` or :meth:`~calendaring_jmap.async_client.AsyncJMAPClient.get_calendars`, without changing anything at runtime.

Why there's a fixup step
========================

Servers can return iCalendar data that doesn't fully comply with :rfc:`5545`: a missing ``DTSTAMP``, a duplicated line, a date field out of the valid range. calendaring-jmap corrects a small, fixed set of known issues before handing iCalendar strings back to callers, rather than surfacing a parse error for data a real server actually sent. See :doc:`../security` for why these corrections are implemented as simple, non-backtracking regular expressions.
