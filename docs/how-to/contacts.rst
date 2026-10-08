.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

==================
Work with contacts
==================

List address books
==================

.. code-block:: python

    address_books = client.get_address_books()
    for book in address_books:
        print(book.id, book.name)

If the account doesn't advertise Contacts support, this returns ``[]`` instead of raising. Pass ``account_id`` to browse an address book another user has shared with you.

Search contacts
===============

.. code-block:: python

    results = client.search_contacts(text="alice")
    results = client.search_contacts(email="alice@example.com")

    for contact in results:
        print(contact.display_name(), contact.primary_email())

Omitting both ``text`` and ``email`` returns every contact in the account. Unlike :meth:`~calendaring_jmap.client.JMAPClient.get_address_books`, an account that doesn't support Contacts raises here rather than returning an empty list, since a caller searching for a contact is usually about to act on the result.

``email`` matches as a substring against any address on the card, on both Cyrus and Stalwart. ``text`` matches as a substring against the whole card on Cyrus, including the name, but on Stalwart it only matches the email address, not the name: a name-only search that finds a contact on Cyrus can silently return nothing on Stalwart. Use ``email`` whenever the value you're searching for might be an email address.
