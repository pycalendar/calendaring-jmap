# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
JMAP CalendarEvent method builders and response parsers.

These are pure functions: no HTTP, no state. They build the request
tuples that go into a ``methodCalls`` list, and parse the corresponding
``methodResponses`` entries.

Method shapes follow :rfc:`8620#section-3.3` (get), :rfc:`8620#section-3.4`
(changes), :rfc:`8620#section-3.5` (set), :rfc:`8620#section-3.6` (query),
:rfc:`8620#section-3.7` (queryChanges); CalendarEvent-specific properties
are defined in the JMAP Calendars specification.
"""

from __future__ import annotations

from calendaring_jmap._methods import (
    build_get,
    build_get_by_query_result,
    build_query,
    parse_set_response,
)

## call_id of the CalendarEvent/query call built by build_event_query(), and
## of the result-referencing CalendarEvent/get call in
## build_event_get_by_query_result(). Named so the two stay in sync by
## construction instead of by two separately hardcoded string literals.
_EVENT_QUERY_CALL_ID = "ev-query-0"
_EVENT_GET_BY_QUERY_CALL_ID = "ev-get-1"


def build_event_get(
    account_id: str,
    ids: list[str] | None = None,
    properties: list[str] | None = None,
) -> tuple:
    """Build a ``CalendarEvent/get`` method call tuple.

    Args:
        account_id: The JMAP accountId to query.
        ids: List of event IDs to fetch, or ``None`` to fetch all.
        properties: List of property names to return, or ``None`` for all.

    Returns:
        A 3-tuple ``("CalendarEvent/get", arguments_dict, call_id)`` suitable
        for inclusion in a ``methodCalls`` list.
    """
    return build_get("CalendarEvent/get", "ev-get-0", account_id, ids, properties)


def parse_event_get(response_args: dict) -> list[dict]:
    """Parse the arguments dict from a ``CalendarEvent/get`` method response.

    Args:
        response_args: The second element of a ``methodResponses`` entry
            whose method name is ``"CalendarEvent/get"``.

    Returns:
        List of raw JSCalendar dicts as returned by the server.
        Returns an empty list if ``"list"`` is absent or empty.
    """
    return list(response_args.get("list", []))


def build_event_changes(
    account_id: str,
    since_state: str,
    max_changes: int | None = None,
) -> tuple:
    """Build a ``CalendarEvent/changes`` method call tuple.

    Args:
        account_id: The JMAP accountId to query.
        since_state: The ``state`` string from a previous ``CalendarEvent/get``
            or ``CalendarEvent/changes`` response.
        max_changes: Optional upper bound on the number of changes returned.
            The server may return fewer.

    Returns:
        A 3-tuple ``("CalendarEvent/changes", arguments_dict, call_id)``.
    """
    args: dict = {"accountId": account_id, "sinceState": since_state}
    if max_changes is not None:
        args["maxChanges"] = max_changes
    return ("CalendarEvent/changes", args, "ev-changes-0")


def parse_event_changes(
    response_args: dict,
) -> tuple[str, str, bool, list[str], list[str], list[str]]:
    """Parse the arguments dict from a ``CalendarEvent/changes`` response.

    Args:
        response_args: The second element of a ``methodResponses`` entry
            whose method name is ``"CalendarEvent/changes"``.

    Returns:
        A 6-tuple ``(old_state, new_state, has_more_changes, created, updated, destroyed)``:

        - ``old_state``: Echo of the ``sinceState`` argument.
        - ``new_state``: State string to store as the next sync token.
        - ``has_more_changes``: True if the server capped the response.
        - ``created``: IDs of newly created events.
        - ``updated``: IDs of modified events.
        - ``destroyed``: IDs of deleted events.
    """
    return (
        response_args.get("oldState", ""),
        response_args.get("newState", ""),
        response_args.get("hasMoreChanges", False),
        response_args.get("created") or [],
        response_args.get("updated") or [],
        response_args.get("destroyed") or [],
    )


def build_event_query(
    account_id: str,
    filter_condition: dict | None = None,
    sort: list[dict] | None = None,
    position: int = 0,
    limit: int | None = None,
    expand_recurrences: bool = False,
    time_zone: str | None = None,
) -> tuple:
    """Build a ``CalendarEvent/query`` method call tuple.

    Args:
        account_id: The JMAP accountId to query.
        filter_condition: A ``FilterCondition`` or ``FilterOperator`` dict, e.g.
            ``{"after": "2024-01-01T00:00:00", "before": "2024-12-31T23:59:59"}``.
            ``after``/``before`` are ``LocalDateTime`` (no ``Z``/UTC suffix;
            confirmed live against Cyrus, which rejects a ``Z``-suffixed
            value with ``invalidArguments``). ``None`` means no filter
            (return all events).
        sort: List of ``Comparator`` dicts, e.g.
            ``[{"property": "start", "isAscending": True}]``.
            ``None`` means server default ordering.
        position: Zero-based index of the first result to return.
        limit: Maximum number of IDs to return. ``None`` means no limit.
        expand_recurrences: If true, the server returns one synthetic id per
            matching occurrence of a recurring event instead of a single id
            for the whole series (draft-ietf-jmap-calendars section 5.11). ``filter_condition``
            must then include both ``after`` and ``before``, or the server
            rejects the call with ``invalidArguments`` (confirmed live).
            Without this, a recurring series is still found by ``after``/
            ``before`` but returned as one object carrying only the master
            occurrence's own ``start``, not whichever occurrence(s) actually
            fall in the window.
        time_zone: The time zone for ``after``/``before``, defaults to
            ``Etc/UTC`` server-side if omitted.

    Returns:
        A 3-tuple ``("CalendarEvent/query", arguments_dict, call_id)``.
    """
    method, args, call_id = build_query(
        "CalendarEvent/query",
        _EVENT_QUERY_CALL_ID,
        account_id,
        filter_condition,
        sort,
        position,
        limit,
    )
    if expand_recurrences:
        args["expandRecurrences"] = expand_recurrences
    if time_zone is not None:
        args["timeZone"] = time_zone
    return (method, args, call_id)


def build_event_get_by_query_result(account_id: str, properties: list[str] | None = None) -> tuple:
    """Build a ``CalendarEvent/get`` call that back-references the ids from
    the ``CalendarEvent/query`` call :func:`build_event_query` builds.

    Args:
        account_id: The JMAP accountId, must match the query call's.
        properties: List of property names to return, or ``None`` for all.

    Returns:
        A 3-tuple ``("CalendarEvent/get", arguments_dict, call_id)``, meant
        to be appended after :func:`build_event_query`'s own return value in
        the same ``methodCalls`` list.
    """
    return build_get_by_query_result(
        "CalendarEvent/get",
        _EVENT_GET_BY_QUERY_CALL_ID,
        "CalendarEvent/query",
        _EVENT_QUERY_CALL_ID,
        account_id,
        properties,
    )


def build_event_set_create(
    account_id: str,
    events: dict[str, dict],
    send_scheduling_messages: bool = False,
) -> tuple:
    """Build a ``CalendarEvent/set`` method call for creating events.

    Args:
        account_id: The JMAP accountId.
        events: Map of client-assigned creation ID to JSCalendar dict.
            The creation IDs are ephemeral, used only to correlate server
            responses with individual creation requests within the same
            batch call.
        send_scheduling_messages: If true, the server sends iTIP scheduling
            messages to the event's participants (draft-ietf-jmap-calendars section 5.9).

    Returns:
        A 3-tuple ``("CalendarEvent/set", arguments_dict, call_id)``.
    """
    return (
        "CalendarEvent/set",
        {
            "accountId": account_id,
            "create": dict(events),
            "sendSchedulingMessages": send_scheduling_messages,
        },
        "ev-set-create-0",
    )


def build_event_set_update(
    account_id: str,
    updates: dict[str, dict],
    send_scheduling_messages: bool = False,
) -> tuple:
    """Build a ``CalendarEvent/set`` method call for updating events.

    Args:
        account_id: The JMAP accountId.
        updates: Map of event ID to partial patch dict.  Keys are property
            names (or JSON Pointer paths for nested properties); values are
            the new values.  Use ``None`` as a value to reset a property to
            its server default.
        send_scheduling_messages: If true, the server sends iTIP scheduling
            messages to the event's participants, or back to the organizer
            if this account isn't the event's origin (draft-ietf-jmap-calendars section 5.9).

    Returns:
        A 3-tuple ``("CalendarEvent/set", arguments_dict, call_id)``.
    """
    return (
        "CalendarEvent/set",
        {
            "accountId": account_id,
            "update": updates,
            "sendSchedulingMessages": send_scheduling_messages,
        },
        "ev-set-update-0",
    )


def build_event_set_destroy(
    account_id: str,
    ids: list[str],
    send_scheduling_messages: bool = False,
) -> tuple:
    """Build a ``CalendarEvent/set`` method call for destroying events.

    Args:
        account_id: The JMAP accountId.
        ids: List of event IDs to destroy.
        send_scheduling_messages: If true, and this account is the event's
            origin, the server sends an iTIP CANCEL to the event's
            participants (draft-ietf-jmap-calendars section 5.9.2.2).

    Returns:
        A 3-tuple ``("CalendarEvent/set", arguments_dict, call_id)``.
    """
    return (
        "CalendarEvent/set",
        {
            "accountId": account_id,
            "destroy": ids,
            "sendSchedulingMessages": send_scheduling_messages,
        },
        "ev-set-destroy-0",
    )


def parse_event_set(
    response_args: dict,
) -> tuple[dict, dict, list[str], dict, dict, dict]:
    """Parse the arguments dict from a ``CalendarEvent/set`` method response.

    Returns a 6-tuple ``(created, updated, destroyed, not_created, not_updated, not_destroyed)``.
    See :func:`calendaring_jmap._methods.parse_set_response` for field semantics.
    """
    return parse_set_response(response_args)
