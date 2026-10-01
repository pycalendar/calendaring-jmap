# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
JMAP Task and TaskList method builders and response parsers.

These are pure functions: no HTTP, no state. They build the request
tuples that go into a ``methodCalls`` list, and parse the corresponding
``methodResponses`` entries.

Method shapes follow :rfc:`8620#section-5.1` (get), :rfc:`8620#section-5.2`
(changes), :rfc:`8620#section-5.3` (set), :rfc:`8620#section-5.5` (query);
Task-specific properties are defined in draft-ietf-jmap-tasks (built on
:rfc:`8984`). draft-ietf-jmap-tasks section 4.13 never defines a Task-specific
query filter object (a literal author TODO in the draft text), so
``build_task_query`` accepts only whatever filter properties the caller
already knows the server accepts; no ``TaskFilter`` shape is assumed here.
"""

from __future__ import annotations

from calendaring_jmap._methods import (
    build_get,
    build_get_by_query_result,
    build_query,
    parse_set_response,
)

_TASK_QUERY_CALL_ID = "task-query-0"
_TASK_GET_BY_QUERY_CALL_ID = "task-get-1"


def build_task_list_get(
    account_id: str,
    ids: list[str] | None = None,
    properties: list[str] | None = None,
) -> tuple:
    """Build a ``TaskList/get`` method call tuple.

    Args:
        account_id: The JMAP accountId to query.
        ids: List of task list IDs to fetch, or ``None`` to fetch all.
        properties: List of property names to return, or ``None`` for all.

    Returns:
        A 3-tuple ``("TaskList/get", arguments_dict, call_id)`` suitable
        for inclusion in a ``methodCalls`` list.
    """
    return build_get("TaskList/get", "tasklist-get-0", account_id, ids, properties)


def parse_task_list_get(response_args: dict) -> list[dict]:
    """Parse the arguments dict from a ``TaskList/get`` method response.

    Args:
        response_args: The second element of a ``methodResponses`` entry
            whose method name is ``"TaskList/get"``.

    Returns:
        List of raw JMAP TaskList dicts as returned by the server.
        Returns an empty list if ``"list"`` is absent or empty.
    """
    return list(response_args.get("list", []))


def build_task_get(
    account_id: str,
    ids: list[str] | None = None,
    properties: list[str] | None = None,
) -> tuple:
    """Build a ``Task/get`` method call tuple.

    Args:
        account_id: The JMAP accountId to query.
        ids: List of task IDs to fetch, or ``None`` to fetch all.
        properties: List of property names to return, or ``None`` for all.

    Returns:
        A 3-tuple ``("Task/get", arguments_dict, call_id)``.
    """
    return build_get("Task/get", "task-get-0", account_id, ids, properties)


def parse_task_get(response_args: dict) -> list[dict]:
    """Parse the arguments dict from a ``Task/get`` method response.

    Args:
        response_args: The second element of a ``methodResponses`` entry
            whose method name is ``"Task/get"``.

    Returns:
        List of raw JMAP Task dicts as returned by the server.
        Returns an empty list if ``"list"`` is absent or empty.
    """
    return list(response_args.get("list", []))


def build_task_changes(
    account_id: str,
    since_state: str,
    max_changes: int | None = None,
) -> tuple:
    """Build a ``Task/changes`` method call tuple.

    Args:
        account_id: The JMAP accountId to query.
        since_state: The ``state`` string from a previous ``Task/get`` or
            ``Task/changes`` response.
        max_changes: Optional upper bound on the number of changes returned.
            The server may return fewer.

    Returns:
        A 3-tuple ``("Task/changes", arguments_dict, call_id)``.
    """
    args: dict = {"accountId": account_id, "sinceState": since_state}
    if max_changes is not None:
        args["maxChanges"] = max_changes
    return ("Task/changes", args, "task-changes-0")


def parse_task_changes(
    response_args: dict,
) -> tuple[str, str, bool, list[str], list[str], list[str]]:
    """Parse the arguments dict from a ``Task/changes`` response.

    Args:
        response_args: The second element of a ``methodResponses`` entry
            whose method name is ``"Task/changes"``.

    Returns:
        A 6-tuple ``(old_state, new_state, has_more_changes, created, updated, destroyed)``.
        ``Task/changes`` uses the identical shape as
        :func:`calendaring_jmap._methods.event.parse_event_changes`
        (draft-ietf-jmap-tasks section 4.10, :rfc:`8620#section-5.2`).
    """
    return (
        response_args.get("oldState", ""),
        response_args.get("newState", ""),
        response_args.get("hasMoreChanges", False),
        response_args.get("created") or [],
        response_args.get("updated") or [],
        response_args.get("destroyed") or [],
    )


def build_task_query(
    account_id: str,
    filter_condition: dict | None = None,
    sort: list[dict] | None = None,
    position: int = 0,
    limit: int | None = None,
) -> tuple:
    """Build a ``Task/query`` method call tuple.

    Args:
        account_id: The JMAP accountId to query.
        filter_condition: A filter dict. draft-ietf-jmap-tasks section 4.13 never
            defines a Task-specific filter object; only ``text`` is sent by
            :meth:`~calendaring_jmap.client.JMAPClient.search_tasks`, matching
            what that method actually applies server-side.
        sort: List of ``Comparator`` dicts, e.g.
            ``[{"property": "due", "isAscending": True}]``.
            ``None`` means server default ordering.
        position: Zero-based index of the first result to return.
        limit: Maximum number of IDs to return. ``None`` means no limit.

    Returns:
        A 3-tuple ``("Task/query", arguments_dict, call_id)``.
    """
    return build_query(
        "Task/query", _TASK_QUERY_CALL_ID, account_id, filter_condition, sort, position, limit
    )


def build_task_get_by_query_result(account_id: str, properties: list[str] | None = None) -> tuple:
    """Build a ``Task/get`` call that back-references the ids from
    the ``Task/query`` call :func:`build_task_query` builds.

    Args:
        account_id: The JMAP accountId, must match the query call's.
        properties: List of property names to return, or ``None`` for all.

    Returns:
        A 3-tuple ``("Task/get", arguments_dict, call_id)``, meant
        to be appended after :func:`build_task_query`'s own return value in
        the same ``methodCalls`` list.
    """
    return build_get_by_query_result(
        "Task/get",
        _TASK_GET_BY_QUERY_CALL_ID,
        "Task/query",
        _TASK_QUERY_CALL_ID,
        account_id,
        properties,
    )


def build_task_set_create(
    account_id: str,
    tasks: dict[str, dict],
) -> tuple:
    """Build a ``Task/set`` method call for creating tasks.

    Args:
        account_id: The JMAP accountId.
        tasks: Map of client-assigned creation ID to JMAP Task dict.

    Returns:
        A 3-tuple ``("Task/set", arguments_dict, call_id)``.
    """
    return (
        "Task/set",
        {
            "accountId": account_id,
            "create": dict(tasks),
        },
        "task-set-create-0",
    )


def build_task_set_update(
    account_id: str,
    updates: dict[str, dict],
) -> tuple:
    """Build a ``Task/set`` method call for updating tasks.

    Args:
        account_id: The JMAP accountId.
        updates: Map of task ID to partial patch dict.

    Returns:
        A 3-tuple ``("Task/set", arguments_dict, call_id)``.
    """
    return (
        "Task/set",
        {"accountId": account_id, "update": updates},
        "task-set-update-0",
    )


def build_task_set_destroy(
    account_id: str,
    ids: list[str],
) -> tuple:
    """Build a ``Task/set`` method call for destroying tasks.

    Args:
        account_id: The JMAP accountId.
        ids: List of task IDs to destroy.

    Returns:
        A 3-tuple ``("Task/set", arguments_dict, call_id)``.
    """
    return (
        "Task/set",
        {"accountId": account_id, "destroy": ids},
        "task-set-destroy-0",
    )


def parse_task_set(
    response_args: dict,
) -> tuple[dict, dict, list[str], dict, dict, dict]:
    """Parse the arguments dict from a ``Task/set`` method response.

    Returns a 6-tuple ``(created, updated, destroyed, not_created, not_updated, not_destroyed)``.
    See :func:`calendaring_jmap._methods.parse_set_response` for field semantics.
    """
    return parse_set_response(response_args)
