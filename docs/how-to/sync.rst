.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

==================
Sync incrementally
==================

JMAP's state-based sync lets you fetch only what changed since the last call, without scanning the full calendar. See :doc:`../explanation/design` for why this differs from CalDAV-style polling.

Fetch a token, then the delta
=============================

.. code-block:: python

    # Record the current state
    token = client.get_sync_token()

    # ... time passes, events are created, modified, or deleted ...

    # Fetch only the delta
    added, modified, deleted, token = client.get_objects_by_sync_token(token)

    for obj in added:
        print("New:", obj.get_icalendar_instance())
    for obj in modified:
        print("Updated:", obj.get_icalendar_instance())
    for event_id in deleted:
        print("Deleted ID:", event_id)

``added`` and ``modified`` are lists of :class:`~calendaring_jmap.objects.calendar_object.JMAPCalendarObject`. ``deleted`` is a list of event IDs: those objects no longer exist on the server, so their data cannot be fetched. The fourth element is the server's new sync token. Chaining straight from it avoids the race window a separate :meth:`~calendaring_jmap.client.JMAPClient.get_sync_token` round trip would open.

Handle a truncated change list
==============================

:meth:`~calendaring_jmap.client.JMAPClient.get_objects_by_sync_token` raises :class:`~calendaring_jmap.error.JMAPMethodError` (``error_type="serverPartialFail"``) if the server truncated the change list (``hasMoreChanges: true``). If this happens, call :meth:`~calendaring_jmap.client.JMAPClient.get_sync_token` to establish a fresh baseline and re-sync from scratch. See :doc:`errors` for other errors this can raise.

Persist the token between runs
==============================

.. code-block:: python

    import json
    import pathlib

    TOKEN_FILE = pathlib.Path("sync_token.json")


    def load_token():
        if TOKEN_FILE.exists():
            return json.loads(TOKEN_FILE.read_text())["token"]
        return None


    def save_token(token):
        TOKEN_FILE.write_text(json.dumps({"token": token}))


    token = load_token()
    if token is None:
        token = client.get_sync_token()
        save_token(token)
    else:
        added, modified, deleted, token = client.get_objects_by_sync_token(token)
        # process changes here
        save_token(token)
