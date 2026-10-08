.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

=====================
Work with attachments
=====================

Upload and attach a file
========================

.. code-block:: python

    with open("agenda.pdf", "rb") as f:
        blob_id = client.upload_attachment(f.read(), content_type="application/pdf")

    client.attach_to_event(
        event_id,
        blob_id=blob_id,
        name="Agenda",
        content_type="application/pdf",
    )

``upload_attachment`` is a raw HTTP upload, not a JMAP method call; it returns a blob ID to pass to ``attach_to_event`` or :meth:`~calendaring_jmap.client.JMAPClient.download_attachment`.

``attach_to_event`` reads the event's current ``links``, then replaces the whole property with the merged result. There's no concurrency guard: a second concurrent call to this method for the same event can silently overwrite the first. Serialize calls for a given event if that matters to you.

List an event's attachments
===========================

.. code-block:: python

    attachments = client.get_event_attachments(event_id)
    for attachment in attachments:
        print(attachment.title, attachment.href)

Download an attachment
======================

.. code-block:: python

    data = client.download_attachment(blob_id, content_type="application/pdf", filename="agenda.pdf")

``content_type`` and ``filename`` only affect the response's own headers; an omitted or wrong value doesn't affect whether the download succeeds.

Cyrus limitation
================

Cyrus accepts an attachment write but doesn't reliably persist the attachment's ``rel: "enclosure"`` marker, so :meth:`~calendaring_jmap.client.JMAPClient.get_event_attachments` may not find an attachment ``attach_to_event`` just wrote. Stalwart is unaffected.
