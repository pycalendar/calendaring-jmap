.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

==================
Push notifications
==================

Subscribing requires a publicly reachable HTTPS endpoint the server can POST to; it won't work behind localhost without tunneling.

Subscribe and verify
====================

.. code-block:: python

    subscription_id = client.subscribe_push(
        callback_url="https://example.com/jmap-push",
        device_client_id="my-app-device-12345",
    )

``device_client_id`` is a value you choose, not one the client generates for you: the spec requires it to differ per device and per vendor, and to not contain an unobfuscated device ID, so this client can't generate a safe one on your behalf. Neither test server substitutes or validates it, so a fixed value reused across installs will collide across devices.

Creating a subscription doesn't start push notifications immediately. The server POSTs a ``PushVerification`` object, containing a ``verificationCode``, to ``callback_url`` right after this call. Your own ``callback_url`` endpoint has to receive that POST and extract the code: this client is an API client, not an HTTP server, so it can't receive the POST itself.

.. code-block:: python

    # In your callback_url endpoint's handler, after extracting
    # verification_code from the PushVerification payload it received:
    client.confirm_push_verification(subscription_id, verification_code)

The server won't push any further notifications until this call succeeds. Stalwart rejects a wrong code with ``invalidProperties`` rather than silently ignoring it; Cyrus doesn't implement ``PushSubscription`` at all, so this doesn't arise there.

Renew a subscription
====================

.. code-block:: python

    client.renew_push(subscription_id, expires="2026-03-01T00:00:00Z")

Pass ``expires=None`` to request no expiration at all. The server may cap or otherwise modify what you ask for.

Unsubscribe
===========

.. code-block:: python

    client.unsubscribe_push(subscription_id)

Server support
==============

Cyrus doesn't implement ``PushSubscription/set`` at all (``unknownMethod`` on every call in this section, tested directly). Stalwart implements the full lifecycle correctly, but echoes an ``accountId`` in every response even though PushSubscription isn't account-scoped and the spec says it shouldn't carry one; this client ignores it.
