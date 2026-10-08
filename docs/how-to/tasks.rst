.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

=========
Use tasks
=========

Task support requires a server implementing ``urn:ietf:params:jmap:tasks`` (the JMAP Tasks specification). If the server doesn't support this capability, :meth:`~calendaring_jmap.client.JMAPClient.get_task_lists` raises :class:`~calendaring_jmap.error.JMAPMethodError`.

.. note::

   ``urn:ietf:params:jmap:tasks`` is an expired IETF draft with no known public server implementation, including Cyrus and Stalwart. The methods on this page are covered by unit tests with mocked responses, but haven't been verified against a live server.

Task lists and tasks are returned as raw dicts using JMAP wire property names, not typed objects.

List task lists
===============

.. code-block:: python

    task_lists = client.get_task_lists()
    for tl in task_lists:
        print(tl["id"], tl["name"])

Create a task
=============

``title`` is required; everything else is optional.

.. code-block:: python

    task_list_id = task_lists[0]["id"]

    task_id = client.create_task(
        task_list_id,
        title="Review pull request",
        due="2026-02-15T17:00:00",
        timeZone="Europe/Oslo",
    )

Optional keyword arguments for :meth:`~calendaring_jmap.client.JMAPClient.create_task` use JMAP wire property names, not snake_case: ``description``, ``start``, ``due``, ``timeZone``, ``estimatedDuration``, ``percentComplete``, ``progress``, ``priority``.

Fetch a task
============

.. code-block:: python

    task = client.get_task(task_id)
    print(task["title"])
    print(task.get("progress", "needs-action"))
    print(task.get("percentComplete", 0))

Update a task
=============

Pass a partial patch dict using JMAP wire property names:

.. code-block:: python

    client.update_task(task_id, {"progress": "completed", "percentComplete": 100})

Delete a task
=============

.. code-block:: python

    client.delete_task(task_id)

Sync incrementally
==================

.. code-block:: python

    token = client.get_task_sync_token()

    # ... time passes, tasks are created, modified, or deleted ...

    added, modified, deleted, token = client.get_tasks_by_sync_token(token)

``added`` and ``modified`` are raw JMAP Task dicts. ``deleted`` is a list of task IDs. Chaining the sync token straight from the previous call's return value, rather than fetching a fresh one with :meth:`~calendaring_jmap.client.JMAPClient.get_task_sync_token`, avoids the race window a separate round trip would open. See :doc:`sync` for the same pattern applied to events, including how to persist a token between runs.

:meth:`~calendaring_jmap.client.JMAPClient.get_tasks_by_sync_token` raises :class:`~calendaring_jmap.error.JMAPMethodError` if the server reports ``hasMoreChanges: true``; call :meth:`~calendaring_jmap.client.JMAPClient.get_task_sync_token` for a fresh baseline and re-sync from scratch.

Search tasks
============

.. code-block:: python

    results = client.search_tasks(text="pull request")
    results = client.search_tasks(due_before="2026-03-01T00:00:00")
    results = client.search_tasks(progress="needs-action")

Omitting every argument returns every task in the account. All filters combine as AND.

``due_before`` and ``due_after`` compare against each task's ``due`` as a plain string; a task with no ``due`` set never matches either filter. ``progress`` treats an absent value as ``"needs-action"``, the spec's own default for a task with no participants, which covers every task this client can create.
