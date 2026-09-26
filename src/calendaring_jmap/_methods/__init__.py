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

    Shared by every ``build_*_get`` function in this package: the shape is
    identical for every object type, only ``method`` and ``call_id`` differ.

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


def build_query(
    method: str,
    call_id: str,
    account_id: str,
    filter_condition: dict | None = None,
    sort: list[dict] | None = None,
    position: int = 0,
    limit: int | None = None,
) -> tuple:
    """Build a ``<Object>/query`` method call tuple.

    Shared by every ``build_*_query`` function in this package: the shape
    of the standard :rfc:`8620#section-5.5` arguments is identical for all
    of them. A function with extra object-specific arguments beyond these
    (``build_event_query``'s own ``expandRecurrences``/``timeZone``) calls
    this for the shared part, then adds its own keys to the returned
    ``arguments_dict`` before returning.

    Args:
        method: The JMAP method name, e.g. ``"ContactCard/query"``.
        call_id: The call id to use in the returned tuple.
        account_id: The JMAP accountId to query.
        filter_condition: A ``FilterCondition`` or ``FilterOperator`` dict.
            ``None`` means no filter (matches every object).
        sort: List of ``Comparator`` dicts. ``None`` means server default
            ordering.
        position: Zero-based index of the first result to return.
        limit: Maximum number of IDs to return. ``None`` means no limit.

    Returns:
        A 3-tuple ``(method, arguments_dict, call_id)``.
    """
    args: dict = {"accountId": account_id, "position": position}
    if filter_condition is not None:
        args["filter"] = filter_condition
    if sort is not None:
        args["sort"] = sort
    if limit is not None:
        args["limit"] = limit
    return (method, args, call_id)


def build_get_by_query_result(
    get_method: str,
    get_call_id: str,
    query_method: str,
    query_call_id: str,
    account_id: str,
    properties: list[str] | None = None,
) -> tuple:
    """Build a ``<Object>/get`` call that back-references the ids from a
    ``<Object>/query`` call built with a matching ``query_call_id``.

    Shared by every ``build_*_get_by_query_result`` function in this
    package. Uses a JMAP result reference (:rfc:`8620#section-3.7`) instead
    of a literal ``ids`` list, so the two calls can be batched into one HTTP
    request without a round trip between them.

    Args:
        get_method: The JMAP get method name, e.g. ``"ContactCard/get"``.
        get_call_id: The call id to use in the returned tuple.
        query_method: The JMAP query method name the result reference
            points at, e.g. ``"ContactCard/query"``.
        query_call_id: The call id of the query call the result reference
            points at.
        account_id: The JMAP accountId, must match the query call's.
        properties: List of property names to return, or ``None`` for all.

    Returns:
        A 3-tuple ``(get_method, arguments_dict, get_call_id)``, meant to
        be appended after the query call's own return value in the same
        ``methodCalls`` list.
    """
    args: dict = {
        "accountId": account_id,
        "#ids": {"resultOf": query_call_id, "name": query_method, "path": "/ids"},
    }
    if properties is not None:
        args["properties"] = properties
    return (get_method, args, get_call_id)


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
