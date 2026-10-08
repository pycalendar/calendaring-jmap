.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

==========================
Scheduling and invitations
==========================

Send an invitation
==================

.. code-block:: python

    event_id = client.send_invite(calendar_id, ical_str)

Identical to :meth:`~calendaring_jmap.client.JMAPClient.create_event`, except the server dispatches iTIP invitations to the event's participants (ATTENDEE/ORGANIZER properties in ``ical_str``). Use :meth:`~calendaring_jmap.client.JMAPClient.create_event` itself for an event with no participants to notify.

Respond to an invitation
========================

.. code-block:: python

    client.accept_invitation(event_id, own_email="alice@example.com")
    client.decline_invitation(event_id, own_email="alice@example.com")
    client.tentatively_accept(event_id, own_email="alice@example.com")

Each finds the participant on the event matching ``own_email`` and sets only that participant's own ``participationStatus``, notifying the organizer. A non-origin account can never modify any other participant property.

There's no counter-proposal method: neither RFC 8984 nor the JMAP Calendars draft define an iTIP COUNTER equivalent. Writing ``start``/``duration`` directly as a non-origin account works through the server's own rights model on both Cyrus and Stalwart, but it's a plain update outside the scheduling methods above, not a supported scheduling primitive, so it isn't exposed as a method here.

Read participant status
=======================

.. code-block:: python

    from calendaring_jmap.constants import PARTICIPATION_STATUS_ACCEPTED

    obj = client.get_event(event_id)
    for participant in obj.get_data().get("participants", {}).values():
        if participant.get("participationStatus") == PARTICIPATION_STATUS_ACCEPTED:
            print(participant.get("email"), "has accepted")

``participants`` is a map keyed by server-assigned participant ID. An absent ``participationStatus`` means ``"needs-action"`` (:rfc:`8984#section-4.4.6`), the spec's own default for a participant who hasn't responded yet.
