.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

====================
Server compatibility
====================

Cyrus and Stalwart are the two servers calendaring-jmap's integration tests run against. This page collects every confirmed difference between them in one place; each row links to the how-to page that covers it in context.

.. list-table::
   :header-rows: 1
   :widths: 30 25 25 20

   * - Behavior
     - Cyrus
     - Stalwart
     - See
   * - ``PushSubscription/get``/``/set``
     - Not implemented at all (``unknownMethod``)
     - Fully implemented
     - :doc:`../how-to/push-notifications`
   * - Attachment ``rel: "enclosure"`` persistence
     - Accepts the write, doesn't reliably persist it on read-back
     - Persists correctly
     - :doc:`../how-to/attachments`, :doc:`../how-to/events`
   * - ``search_events``'s ``participant_role="attendee"`` filter
     - Server drops the ``attendee`` role from ``roles``, so this misses matches
     - Same gap
     - :doc:`../how-to/events`
   * - Contact search by ``text`` against the card's name
     - Matches as a substring against the name
     - Matches only the email address, never the name
     - :doc:`../how-to/contacts`
   * - ``get_availability`` with ``show_details=True``
     - Returns event details by default
     - Needs the account to support returning details at all; ``event`` comes back ``None`` otherwise
     - :doc:`../how-to/free-busy`
   * - Participant ``sendTo`` on ``CalendarEvent/set``
     - Rejected outright with ``invalidProperties``; ``calendarAddress`` required instead
     - Accepts the ``calendarAddress``-only form too
     - :doc:`../explanation/design`
   * - JMAP Tasks (``urn:ietf:params:jmap:tasks``)
     - Not implemented
     - Not implemented
     - :doc:`../how-to/tasks`

Counter-proposals (iTIP COUNTER) have no distinct method on either server: a non-origin participant can write ``start``/``duration`` directly as a plain update, through the server's own rights model, on both Cyrus and Stalwart. See :doc:`../how-to/scheduling`.
