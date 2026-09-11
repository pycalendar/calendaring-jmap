.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

==============
List calendars
==============

.. code-block:: python

    calendars = client.get_calendars()
    for cal in calendars:
        print(cal.id, cal.name, cal.color)

Each item is a :class:`~calendaring_jmap.objects.calendar.JMAPCalendar`; see :doc:`../reference/objects` for its fields.
