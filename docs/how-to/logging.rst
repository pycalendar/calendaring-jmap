.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

==============
Enable logging
==============

calendaring-jmap logs through the standard library under the ``calendaring_jmap`` logger name, both the sync and async client:

.. code-block:: python

    import logging

    logging.getLogger("calendaring_jmap").setLevel(logging.DEBUG)
    logging.basicConfig()

At ``DEBUG``, every JMAP request logs its destination URL and how many method calls it batched. At ``WARNING`` (the level a caller typically cares about by default), the library logs the cases below, each one a place where it silently chose a fallback instead of raising.

Conversion warnings
===================

``ical_to_jscal`` logs a warning, rather than raising, for input that's technically malformed but recoverable:

- More than one ``RRULE``/``EXRULE`` line on a VEVENT: only the first is kept.
- More than one ``URL`` line on a VEVENT (RFC 5545 section 3.8.4.6 allows at most one): only the first is kept.
- More than one ``VCONFERENCE`` subcomponent sharing the same URI: only the first is kept.
- A ``VALARM`` with no ``TRIGGER`` property: that one alarm is skipped; the rest of the event still converts.

``fixup`` (the step that corrects non-compliant iCalendar before conversion; see :doc:`../explanation/design` for why it exists) logs when it actually changes something, at a rate-limited level. Each power-of-two occurrence (the first, second, fourth, eighth, and further doublings) logs at ``WARNING``; every other occurrence logs at ``DEBUG``. This keeps a feed with many non-compliant events from flooding your logs with the same warning, while still surfacing it periodically instead of going silent after the first one.

Account-level fallbacks
=======================

``get_address_books`` logs a warning and returns an empty list, rather than raising, when the session's own account doesn't advertise Contacts support; see :doc:`contacts` for when this applies versus when it raises instead.
