# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
JMAP Calendar method builders and response parsers.

These are pure functions — no HTTP, no state. They build the request
tuples that go into a ``methodCalls`` list, and parse the corresponding
``methodResponses`` entries.

Method shapes follow RFC 8620 §3.3 (get), §3.4 (changes), §3.5 (set); Calendar-specific
properties are defined in the JMAP Calendars specification.
"""

from __future__ import annotations

from calendaring_jmap._methods import parse_set_response
from calendaring_jmap.objects.calendar import JMAPCalendar


def build_calendar_get(
    account_id: str,
    ids: list[str] | None = None,
    properties: list[str] | None = None,
) -> tuple:
    """Build a ``Calendar/get`` method call tuple.

    Args:
        account_id: The JMAP accountId to query.
        ids: List of calendar IDs to fetch, or ``None`` to fetch all.
        properties: List of property names to return, or ``None`` for all.

    Returns:
        A 3-tuple ``("Calendar/get", arguments_dict, call_id)`` suitable
        for inclusion in a ``methodCalls`` list.
    """
    args: dict = {"accountId": account_id, "ids": ids}
    if properties is not None:
        args["properties"] = properties
    return ("Calendar/get", args, "cal-get-0")


def parse_calendar_get(response_args: dict) -> list[JMAPCalendar]:
    """Parse the arguments dict from a ``Calendar/get`` method response.

    Args:
        response_args: The second element of a ``methodResponses`` entry
            whose method name is ``"Calendar/get"``.

    Returns:
        List of :class:`~calendaring_jmap.objects.calendar.JMAPCalendar` objects.
        Returns an empty list if ``"list"`` is absent or empty.
    """
    return [JMAPCalendar.from_jmap(item) for item in response_args.get("list", [])]


def build_calendar_changes(account_id: str, since_state: str) -> tuple:
    """Build a ``Calendar/changes`` method call tuple.

    Args:
        account_id: The JMAP accountId to query.
        since_state: The ``state`` string from a previous ``Calendar/get``
            or ``Calendar/changes`` response.

    Returns:
        A 3-tuple ``("Calendar/changes", arguments_dict, call_id)``.
    """
    return (
        "Calendar/changes",
        {"accountId": account_id, "sinceState": since_state},
        "cal-changes-0",
    )


def build_calendar_set_create(account_id: str, calendars: dict[str, dict]) -> tuple:
    """Build a ``Calendar/set`` method call for creating calendars.

    Args:
        account_id: The JMAP accountId.
        calendars: Map of client-assigned creation ID to Calendar JSON dict.

    Returns:
        A 3-tuple ``("Calendar/set", arguments_dict, call_id)``.
    """
    return (
        "Calendar/set",
        {"accountId": account_id, "create": dict(calendars)},
        "cal-set-create-0",
    )


def build_calendar_set_update(account_id: str, updates: dict[str, dict]) -> tuple:
    """Build a ``Calendar/set`` method call for updating calendars.

    Args:
        account_id: The JMAP accountId.
        updates: Map of calendar ID to partial patch dict.

    Returns:
        A 3-tuple ``("Calendar/set", arguments_dict, call_id)``.
    """
    return (
        "Calendar/set",
        {"accountId": account_id, "update": updates},
        "cal-set-update-0",
    )


def build_calendar_set_destroy(
    account_id: str,
    ids: list[str],
    on_destroy_remove_events: bool = False,
) -> tuple:
    """Build a ``Calendar/set`` method call for destroying calendars.

    Args:
        account_id: The JMAP accountId.
        ids: List of calendar IDs to destroy.
        on_destroy_remove_events: If ``False`` (the spec default), destroying
            a calendar that still has events fails with a ``calendarHasEvent``
            SetError. If ``True``, the events are removed along with it.

    Returns:
        A 3-tuple ``("Calendar/set", arguments_dict, call_id)``.
    """
    return (
        "Calendar/set",
        {
            "accountId": account_id,
            "destroy": ids,
            "onDestroyRemoveEvents": on_destroy_remove_events,
        },
        "cal-set-destroy-0",
    )


def parse_calendar_set(
    response_args: dict,
) -> tuple[dict, dict, list[str], dict, dict, dict]:
    """Parse the arguments dict from a ``Calendar/set`` method response.

    Returns a 6-tuple ``(created, updated, destroyed, not_created, not_updated, not_destroyed)``.
    See :func:`calendaring_jmap._methods.parse_set_response` for field semantics.
    """
    return parse_set_response(response_args)
