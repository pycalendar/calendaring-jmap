.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

=========
Use tasks
=========

Task support requires a server implementing ``urn:ietf:params:jmap:tasks`` (the JMAP Tasks specification). If the server does not support this capability, :meth:`~calendaring_jmap.client.JMAPClient.get_task_lists` raises :class:`~calendaring_jmap.error.JMAPMethodError`.

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
        time_zone="Europe/Oslo",
    )

Optional keyword arguments for :meth:`~calendaring_jmap.client.JMAPClient.create_task`: ``description``, ``start``, ``due``, ``time_zone``, ``estimated_duration``, ``percent_complete``, ``progress``, ``priority``.

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
