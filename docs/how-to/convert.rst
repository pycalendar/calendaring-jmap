.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

================================
Convert iCalendar and JSCalendar
================================

``ical_to_jscal`` and ``jscal_to_ical`` are the same conversion functions the client uses internally, available directly if you want to convert between formats without making any JMAP request.

.. code-block:: python

    from calendaring_jmap.convert import ical_to_jscal, jscal_to_ical

    jscal = ical_to_jscal(ical_str)
    ical_str_again = jscal_to_ical(jscal)

``ical_to_jscal`` processes the first VEVENT in the string; any sibling VEVENTs sharing the same UID with a RECURRENCE-ID are folded into the result's ``recurrenceOverrides`` map, and EXDATE lines become excluded override entries. Pass ``calendar_id`` if the result is going to a real ``CalendarEvent/set`` call, since the server needs ``calendarIds`` set on it:

.. code-block:: python

    jscal = ical_to_jscal(ical_str, calendar_id=calendar_id)

Both functions raise ``ValueError`` on malformed input, the same errors :doc:`errors` describes for ``create_event`` and ``update_event``, since those methods call ``ical_to_jscal`` internally. See :doc:`../explanation/design` for the conversion layer's own design decisions.
