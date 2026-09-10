.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

=====
Tasks
=====

Task support requires a server implementing ``urn:ietf:params:jmap:tasks`` (the JMAP Tasks specification). If the server does not support this capability, :meth:`~calendaring_jmap.client.JMAPClient.get_task_lists` raises :class:`~calendaring_jmap.error.JMAPMethodError`.

Task lists and tasks are returned as raw dicts using JMAP wire property names, not typed objects.

.. code-block:: python

    # List task lists
    task_lists = client.get_task_lists()
    for tl in task_lists:
        print(tl["id"], tl["name"])

    task_list_id = task_lists[0]["id"]

    # Create a task: title is required, everything else is optional
    task_id = client.create_task(
        task_list_id,
        title="Review pull request",
        due="2026-02-15T17:00:00",
        time_zone="Europe/Oslo",
    )

    # Fetch
    task = client.get_task(task_id)
    print(task["title"])
    print(task.get("progress", "needs-action"))
    print(task.get("percentComplete", 0))

    # Update: pass a partial patch dict using JMAP wire property names
    client.update_task(task_id, {"progress": "completed", "percentComplete": 100})

    # Delete
    client.delete_task(task_id)

Optional keyword arguments for :meth:`~calendaring_jmap.client.JMAPClient.create_task`: ``description``, ``start``, ``due``, ``time_zone``, ``estimated_duration``, ``percent_complete``, ``progress``, ``priority``.
