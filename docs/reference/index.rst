.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

:mod:`calendaring_jmap`: Package entry points
=============================================

.. meta::
   :description: get_jmap_client, get_async_jmap_client, and get_connection_params, the top-level functions that read configuration and return a connected client.

``get_jmap_client``, ``get_async_jmap_client``, and ``get_connection_params`` are the functions most code imports directly from ``calendaring_jmap``.

.. autofunction:: calendaring_jmap.get_jmap_client

.. autofunction:: calendaring_jmap.get_async_jmap_client

.. autofunction:: calendaring_jmap.get_connection_params

Everything else exported from ``calendaring_jmap`` (:class:`~calendaring_jmap.client.JMAPClient`, :class:`~calendaring_jmap.async_client.AsyncJMAPClient`, the calendar objects, and the error hierarchy) has its own page, listed below.

.. toctree::
   :maxdepth: 1

   client
   async_client
   objects
   convert
   session
   constants
   errors
   server-compatibility
   ../changelog
