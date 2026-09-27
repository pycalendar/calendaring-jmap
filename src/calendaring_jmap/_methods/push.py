# SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
JMAP PushSubscription method builders and response parsers.

Pure functions: no HTTP, no state. PushSubscription is defined in
:rfc:`8620#section-7.2`, part of JMAP core rather than a capability-gated
extension: no capability URN beyond ``urn:ietf:params:jmap:core`` is ever
required for these methods.

Unlike every other object type in this package, ``PushSubscription/get``
and ``PushSubscription/set`` neither take nor return an ``accountId``
argument (:rfc:`8620#section-7.2.1`, :rfc:`8620#section-7.2.2`): a push
subscription is tied to the credentials that created it, not to a
specific account. The ``/set`` builders below (this client has no need
for a ``PushSubscription/get`` builder; see the client's own
``subscribe_push``/``renew_push``/``unsubscribe_push``, which never list
existing subscriptions) omit ``accountId`` entirely for this reason,
unlike ``build_get``/``build_query`` in this package's ``__init__.py``,
which always include it.

Confirmed live (2026-09) that Cyrus does not implement ``PushSubscription/get``
or ``/set`` at all (``unknownMethod`` on both). Stalwart implements both
correctly, but echoes an ``accountId`` key in every ``/set`` response
despite the RFC's text that the method does not return one;
``parse_push_subscription_set`` never reads that key, so this is
harmless and requires no special handling here.

One ``build_push_subscription_set_*`` function per create/update/destroy
operation, not a single combined builder, matching every other
``_methods/*.py`` object type's own create/update/destroy split
(``build_calendar_set_create``/``_update``/``_destroy``,
``build_task_set_create``/``_update``/``_destroy``). One parser,
``parse_push_subscription_set``, still covers all three, since the
``*/set`` response shape is identical regardless of which operation
produced it.
"""

from __future__ import annotations

from calendaring_jmap._methods import parse_set_response

_PUSH_SET_CREATE_CALL_ID = "push-set-create-0"
_PUSH_SET_UPDATE_CALL_ID = "push-set-update-0"
_PUSH_SET_DESTROY_CALL_ID = "push-set-destroy-0"

#: Client-assigned creation id used for the single subscription created by
#: a ``subscribe_push`` call.
_PUSH_NEW_ID = "new-0"


def build_push_subscription_set_create(
    device_client_id: str,
    url: str,
    types: list[str] | None,
) -> tuple:
    """Build a ``PushSubscription/set`` method call for creating one
    subscription.

    Args:
        device_client_id: Client-chosen id identifying the device/app
            combination creating this subscription (:rfc:`8620#section-7.2`).
        url: The HTTPS URL the server will POST push notifications to.
        types: List of type names to receive notifications for, or
            ``None`` for all types.

    Returns:
        A 3-tuple ``("PushSubscription/set", arguments_dict, call_id)``.
    """
    subscription: dict = {
        "deviceClientId": device_client_id,
        "url": url,
        "types": types,
    }
    return (
        "PushSubscription/set",
        {"create": {_PUSH_NEW_ID: subscription}},
        _PUSH_SET_CREATE_CALL_ID,
    )


def build_push_subscription_set_update(subscription_id: str, patch: dict) -> tuple:
    """Build a ``PushSubscription/set`` method call for updating one
    subscription.

    Args:
        subscription_id: The JMAP PushSubscription ID to update.
        patch: Partial property patch, e.g. ``{"expires": ...}`` or
            ``{"verificationCode": ...}``.

    Returns:
        A 3-tuple ``("PushSubscription/set", arguments_dict, call_id)``.
    """
    return (
        "PushSubscription/set",
        {"update": {subscription_id: patch}},
        _PUSH_SET_UPDATE_CALL_ID,
    )


def build_push_subscription_set_destroy(subscription_id: str) -> tuple:
    """Build a ``PushSubscription/set`` method call for destroying one
    subscription.

    Args:
        subscription_id: The JMAP PushSubscription ID to destroy.

    Returns:
        A 3-tuple ``("PushSubscription/set", arguments_dict, call_id)``.
    """
    return (
        "PushSubscription/set",
        {"destroy": [subscription_id]},
        _PUSH_SET_DESTROY_CALL_ID,
    )


def parse_push_subscription_set(
    response_args: dict,
) -> tuple[dict, dict, list[str], dict, dict, dict]:
    """Parse the arguments dict from a ``PushSubscription/set`` method response.

    Returns a 6-tuple ``(created, updated, destroyed, not_created, not_updated, not_destroyed)``.
    See :func:`calendaring_jmap._methods.parse_set_response` for field semantics.
    """
    return parse_set_response(response_args)
