.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

=========
Calendars
=========

.. code-block:: python

    calendars = client.get_calendars()
    for cal in calendars:
        print(cal.id, cal.name, cal.color)

Each item is a :class:`~calendaring_jmap.objects.calendar.JMAPCalendar` dataclass. The fields are ``id``, ``name``, ``description``, ``color`` (a CSS string or ``None``), ``is_subscribed``, ``my_rights`` (a dict), ``sort_order``, and ``is_visible``.
