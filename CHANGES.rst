.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

==========
Change log
==========

.. py:currentmodule:: calendaring_jmap

.. Do *NOT* add new change log entries to this file.
   Instead create a file in the news directory.
   See CONTRIBUTING.md for the change log entry format.

.. towncrier release notes start

1.0.0 (2026-09-11)
------------------

New features
~~~~~~~~~~~~

- Extracted the JMAP client from python-caldav into this standalone package. calendaring-jmap no longer depends on ``caldav``: configuration now comes from ``JMAP_URL``/``JMAP_USERNAME``/``JMAP_PASSWORD`` environment variables or a YAML config file instead of caldav's config system. (`#9 <https://github.com/pycalendar/calendaring-jmap/issues/9>`_)


Documentation
~~~~~~~~~~~~~

- Added Sphinx documentation scaffolding with the PyData theme. (`#8 <https://github.com/pycalendar/calendaring-jmap/issues/8>`_)
- Added a security policy: how to report a vulnerability, and known risks specific to JMAP session discovery, credential handling, and untrusted calendar data. (`#22 <https://github.com/pycalendar/calendaring-jmap/issues/22>`_)
- Restructured the docs around the Diataxis framework (tutorial, how-to, reference, explanation), added a security policy and NLnet funding acknowledgment, and cleaned up release metadata ahead of 1.0. (`#42 <https://github.com/pycalendar/calendaring-jmap/issues/42>`_)


Other tasks
~~~~~~~~~~~

- Maintenance: type checking, test coverage, Docker test fixtures, packaging fix, contributing docs on Read the Docs. (`#40 <https://github.com/pycalendar/calendaring-jmap/issues/40>`_)
