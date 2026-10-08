.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

======
Design
======

Why JMAP identity is opaque
===========================

JMAP identifies calendars, events, and tasks by opaque server-assigned IDs, not URLs. An object's ID stays the same for its lifetime regardless of where or how the server chooses to store it.

This shapes the client's API: :meth:`~calendaring_jmap.client.JMAPClient.create_event` and similar methods return an ID string, not a URL, and every subsequent lookup, update, or delete uses that ID. There's no separate "fetch the URL, then act on it" step.

Why there's a sync and an async client
======================================

:class:`~calendaring_jmap.async_client.AsyncJMAPClient` mirrors every public method of :class:`~calendaring_jmap.client.JMAPClient` as a coroutine, rather than the library offering only one and asking callers to wrap it. Both share their parsing and request-building logic through a common base class (``_JMAPClientBase``), so the two clients don't duplicate that logic, only the transport call itself differs (blocking request vs. ``await``).

:class:`~calendaring_jmap.objects.calendar.JMAPCalendar` and :class:`~calendaring_jmap.objects.calendar_object.JMAPCalendarObject` need to support both: the same calendar object type is returned whether it came from the sync or the async client, and its methods (``search``, ``add_event``, and similar) need to return a plain value in the sync case and a coroutine in the async case. Python's type system can't narrow a return type based on a runtime flag, so calendaring-jmap uses a generic type parameter on :class:`~calendaring_jmap.objects.calendar.JMAPCalendar` purely for static typing: it lets type checkers give a correctly-typed result depending on whether the calendar came from :meth:`~calendaring_jmap.client.JMAPClient.get_calendars` or :meth:`~calendaring_jmap.async_client.AsyncJMAPClient.get_calendars`, without changing anything at runtime.

Why there's a fixup step
========================

Servers can return iCalendar data that doesn't fully comply with :rfc:`5545`: a missing ``DTSTAMP``, a duplicated line, a date field out of the valid range. calendaring-jmap corrects a small, fixed set of known issues before handing iCalendar strings back to callers, rather than surfacing a parse error for data a real server actually sent. See :doc:`../security` for why these corrections are implemented as simple, non-backtracking regular expressions.

Why CONFERENCE and VCONFERENCE correlate by URI
===============================================

A CONFERENCE property (:rfc:`7986`) and a VCONFERENCE component both describe the same video or phone link, but the spec gives them no shared identifier: iCalendar has no concept of one property pointing at a specific sibling component by ID. The conversion layer matches them by comparing the CONFERENCE property's own value against the VCONFERENCE component's ``URI`` property, string for string. A VCONFERENCE whose URI matches no CONFERENCE property on the same component converts to nothing: there's no VirtualLocation for it to attach its description to.

Within a matched VCONFERENCE, ``STYLED-DESCRIPTION`` (:rfc:`9073`) is preferred over plain ``DESCRIPTION`` when both are present, since only ``STYLED-DESCRIPTION`` carries a content type. The draft's own ``DERIVED`` parameter decides which one actually describes the conference and which one a server generated from the other: a non-derived ``STYLED-DESCRIPTION`` is treated as the one carrying real content, even when a ``DESCRIPTION`` also happens to be present.

Why recurrence override patches compare by value, not by key
============================================================

A recurrence override patch only needs to carry what actually changed on that one occurrence, not the whole event again. For most properties this is a direct comparison: a changed ``title`` string either matches the master's or it doesn't. The map-shaped properties (``participants``, ``locations``, ``alerts``, ``virtualLocations``) don't work that way, since each entry gets a fresh, randomly generated key every time the conversion layer builds one, so the same unchanged location on the master and an override child never share a key even when nothing about it actually changed. Comparing these maps by their sorted canonical content instead of by key-for-key equality is what makes the override diff correct: a location that's genuinely identical on both sides is correctly left out of the patch, and a location that's genuinely different is correctly included.

Why participants carry calendarAddress, not sendTo
==================================================

An ``ORGANIZER``/``ATTENDEE`` value is a URI (:rfc:`5545#section-3.3.3`). It doesn't have to use the ``mailto:`` scheme; ``sip:alice@example.com`` is valid too. JSCalendar's Participant ``email`` property is narrower: :rfc:`8984#section-4.4.6` defines it as an :rfc:`5322#section-3.4.1` addr-spec, not an arbitrary URI. So a non-mailto address converts to ``calendarAddress`` as-is, and ``email`` is left unset rather than filled with a value that isn't actually an email address.

JSCalendar's own Participant object defines ``sendTo`` (a map of delivery method to URI), not ``calendarAddress``; the latter belongs to JMAP Calendars' ParticipantIdentity/Principal objects instead. Cyrus doesn't accept ``sendTo`` at all: ``CalendarEvent/set`` rejects it outright with ``invalidProperties`` on both create and update, and requires ``calendarAddress`` in its place. Tested against a running Cyrus container: ``sendTo`` alone is rejected, ``calendarAddress`` alone is accepted, and having both still gets rejected for ``sendTo``. calendaring-jmap sets ``calendarAddress`` to ``sendTo``'s would-be ``imip`` value, its natural equivalent per ParticipantIdentity's own definition, and omits ``sendTo`` entirely. Stalwart accepts the ``calendarAddress``-only form too.

Why an Alert's trigger is an object, not a string
=================================================

A JSCalendar Alert's ``trigger`` is an OffsetTrigger or AbsoluteTrigger object (:rfc:`8984#section-4.5.2`), not a bare duration or timestamp string. A relative ``VALARM`` ``TRIGGER`` becomes ``{"@type": "OffsetTrigger", "offset": "-PT15M", "relativeTo": "start"}``; an absolute one becomes ``{"@type": "AbsoluteTrigger", "when": "..."}``. ``TRIGGER`` is mandatory on a ``VALARM`` per :rfc:`5545#section-3.6.6` and :rfc:`8984#section-4.5.2` alike, but not every producer enforces it. Rather than failing the whole event's conversion over one malformed sub-item, calendaring-jmap skips just that one alarm, with a warning.

Why Link conversion is narrower than the draft's full mapping
=============================================================

draft-ietf-calext-jscalendar-icalendar section 3.4 defines a full Link/ATTACH/IMAGE/LINK mapping. calendaring-jmap implements only the attachment case its own :meth:`~calendaring_jmap.client.JMAPClient.attach_to_event`/:meth:`~calendaring_jmap.client.JMAPClient.get_event_attachments` need. A URI-form ``ATTACH`` converts to ``href`` as-is. A binary-form ``ATTACH`` (:rfc:`5545#section-3.8.1.1`'s inline ``ENCODING=BASE64;VALUE=BINARY`` form) converts to a ``data:`` URL (:rfc:`2397`) instead, with ``FMTTYPE`` becoming both the data URL's media type and ``contentType``. ``rel`` is always ``"enclosure"``, since an iCalendar ``ATTACH``, unlike JSCalendar's general Link object, has no notion of a link that isn't an attachment; every ``ATTACH`` converts as one, and the reverse only happens for a Link whose ``rel`` is ``"enclosure"``.

A plain ``URL`` property has no defined relationship to the links JSCalendar names (:rfc:`5545#section-3.8.4.6` versus :rfc:`8984#section-4.2.7`'s enclosure/describedby/icon). It converts to a Link with no ``rel`` set, matching the Link object's own optional ``rel`` (:rfc:`8984#section-1.4.11`), and only a Link with no ``rel`` at all converts back to ``URL``.

Why recurrence rule UNTIL sometimes converts time zones
=======================================================

JSCalendar's ``until`` on a RecurrenceRule is a LocalDateTime in the event's own time zone. iCalendar's ``UNTIL`` has a stricter rule: :rfc:`5545#section-3.3.10` requires it to be UTC whenever ``DTSTART`` is a TZID or UTC date-time. So converting a non-UTC ``until`` back to iCalendar resolves it to UTC first, rather than passing the local value through with the wrong implied zone.

Why the error hierarchy is two-tiered
=====================================

:class:`~calendaring_jmap.error.JMAPBaseError` carries only a URL and a reason string; :class:`~calendaring_jmap.error.JMAPError` subclasses it and adds :rfc:`8620`'s ``error_type`` string. The split exists because ``JMAPBaseError`` can optionally subclass an installed ``caldav`` package's own ``DAVError``, giving code that catches ``DAVError`` around CalDAV calls a way to catch these errors too; that subclassing has nothing to do with JMAP's own error-type model, so it lives on the narrower base class, not on ``JMAPError`` itself. Every other error class (:class:`~calendaring_jmap.error.JMAPCapabilityError`, :class:`~calendaring_jmap.error.JMAPAuthError`, :class:`~calendaring_jmap.error.JMAPMethodError`) extends ``JMAPError``, since all three represent an actual JMAP-level failure with a real ``error_type``.
