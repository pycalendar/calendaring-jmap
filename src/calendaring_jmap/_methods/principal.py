# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
JMAP Principal method builders and response parsers.

These are pure functions — no HTTP, no state. They build the request
tuples that go into a ``methodCalls`` list, and parse the corresponding
``methodResponses`` entries.

``Principal/getAvailability`` is defined in draft-ietf-jmap-calendars §2.2,
layered on the ``Principal`` object from RFC 9670 (JMAP Sharing). It is not
a get/set/query method; it takes its own bespoke arguments and returns a
list of ``BusyPeriod`` objects, also defined in that same section.
"""

from __future__ import annotations


def build_get_availability(
    principal_id: str,
    utc_start: str,
    utc_end: str,
    show_details: bool = False,
    event_properties: list[str] | None = None,
) -> tuple:
    """Build a ``Principal/getAvailability`` method call tuple.

    Deliberately has no ``account_id`` parameter, unlike every other
    ``build_*`` function in this package. Confirmed live against Cyrus:
    passing ``accountId`` inside this method's own arguments dict fails
    with ``invalidArguments: ["accountId"]``, even with a verified-correct
    value, while omitting it (so the server falls back to the
    authenticated user's own account) succeeds. Confirmed live against
    Stalwart that passing ``accountId`` explicitly works fine there, so
    omitting it is the one shape that works on both.

    Args:
        principal_id: The id of the ``Principal`` to calculate availability
            for, from ``Session.account_capabilities`` (see
            ``_JMAPClientBase._current_user_principal_id``) or ``Principal/get``.
            Not the same as a JMAP accountId.
        utc_start: Start of the period to check, inclusive, as a
            ``UTCDateTime`` string (e.g. ``"2024-01-01T00:00:00Z"``).
        utc_end: End of the period to check, exclusive, as a ``UTCDateTime``
            string.
        show_details: If true, populate each returned ``BusyPeriod.event``
            with the underlying event, where the caller has ``mayReadItems``
            and the event isn't private. Confirmed live: Stalwart also
            requires ``event_properties`` to be set for this to take
            effect at all; omitting it returns ``event: null`` on Stalwart
            even with ``show_details=True``, unlike Cyrus which returns full
            details by default.
        event_properties: Which ``CalendarEvent`` properties to include in
            each returned event, when ``show_details`` is true. Confirmed
            live: Stalwart only supports ``["id"]``/``["baseEventId"]`` here,
            rejecting other property names with ``invalidArguments``.

    Returns:
        A 3-tuple ``("Principal/getAvailability", arguments_dict, call_id)``.
    """
    args: dict = {"id": principal_id, "utcStart": utc_start, "utcEnd": utc_end}
    if show_details:
        args["showDetails"] = show_details
    if event_properties is not None:
        args["eventProperties"] = event_properties
    return ("Principal/getAvailability", args, "principal-getavailability-0")


def parse_get_availability(response_args: dict) -> list[dict]:
    """Parse the arguments dict from a ``Principal/getAvailability`` response.

    Args:
        response_args: The second element of a ``methodResponses`` entry
            whose method name is ``"Principal/getAvailability"``.

    Returns:
        List of raw ``BusyPeriod`` dicts, each with ``utcStart``, ``utcEnd``,
        ``busyStatus``, ``event`` (``None`` unless ``showDetails`` was set
        and permitted), and ``accountId``. Callers convert these to typed
        :class:`~calendaring_jmap.objects.busy_interval.BusyInterval`
        objects; this function does not.
    """
    return response_args.get("list", [])
