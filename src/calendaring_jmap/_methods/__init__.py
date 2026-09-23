# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations


def build_get(
    method: str,
    call_id: str,
    account_id: str,
    ids: list[str] | None = None,
    properties: list[str] | None = None,
) -> tuple:
    """Build a ``<Object>/get`` method call tuple.

    Shared by every ``build_*_get`` function in this package (``Calendar``,
    ``CalendarEvent``, ``TaskList``, ``Task``): the shape is identical for
    all of them, only ``method`` and ``call_id`` differ per object type.

    Args:
        method: The JMAP method name, e.g. ``"Calendar/get"``.
        call_id: The call id to use in the returned tuple.
        account_id: The JMAP accountId to query.
        ids: List of object IDs to fetch, or ``None`` to fetch all.
        properties: List of property names to return, or ``None`` for all.

    Returns:
        A 3-tuple ``(method, arguments_dict, call_id)`` suitable for
        inclusion in a ``methodCalls`` list.
    """
    args: dict = {"accountId": account_id, "ids": ids}
    if properties is not None:
        args["properties"] = properties
    return (method, args, call_id)


def parse_set_response(response_args: dict) -> tuple[dict, dict, list[str], dict, dict, dict]:
    """Parse the arguments dict from any JMAP ``*/set`` method response.

    Returns a 6-tuple ``(created, updated, destroyed, not_created, not_updated, not_destroyed)``.
    """
    created: dict = response_args.get("created") or {}
    updated: dict = response_args.get("updated") or {}
    destroyed: list[str] = response_args.get("destroyed") or []
    not_created: dict = response_args.get("notCreated") or {}
    not_updated: dict = response_args.get("notUpdated") or {}
    not_destroyed: dict = response_args.get("notDestroyed") or {}
    return created, updated, destroyed, not_created, not_updated, not_destroyed
