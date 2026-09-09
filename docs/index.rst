.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

================
calendaring-jmap
================

Calendar operations over JMAP (:rfc:`8620` + draft-ietf-jmap-calendars), mirroring the
public API of `python-caldav <https://github.com/python-caldav/caldav>`_ so user code
works against either protocol unmodified.

This package is being extracted from python-caldav's ``caldav/jmap/`` module
(`#9 <https://github.com/pycalendar/calendaring-jmap/issues/9>`_) and doesn't have
working code here yet. Usage and API reference docs will be filled in as that lands.

.. toctree::
   :maxdepth: 1
   :caption: Contents

   installation
   usage/quickstart
   usage/calendars
   usage/events
   usage/tasks
   usage/sync
   api/client
   api/async_client
   api/objects
