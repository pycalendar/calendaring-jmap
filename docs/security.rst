.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

===============
Security policy
===============

Report a vulnerability using `GitHub's private vulnerability reporting <https://github.com/pycalendar/calendaring-jmap/security/advisories/new>`_ (the "Report a vulnerability" button on the Security tab), rather than a public issue.

Supported versions
==================

Security fixes target the latest release. There's no long-term support branch.

Known risks and design notes
============================

Server-controlled API URL
-------------------------

:rfc:`8620#section-2` has the client discover its API endpoint at runtime: a GET to ``/.well-known/jmap`` returns a Session object whose ``apiUrl`` field is where every subsequent request goes. This is how JMAP works, not something calendaring-jmap adds on top, and it means the session response controls where credentials get sent next.

calendaring-jmap only corrects ``apiUrl`` when it names the same host as the session endpoint but a different port or scheme (some servers do this). A session response naming a genuinely different host is followed as-is. Combined with TLS, this is safe against a passive network observer; it does not protect against a session endpoint that is itself malicious or already compromised, since that is the entity the protocol asks you to trust. Only point the client at a session URL you control or trust, and use HTTPS for it.

TLS and credentials
-------------------

calendaring-jmap does no custom TLS handling: it relies entirely on its HTTP library (niquests or requests, see :ref:`dependencies`) for certificate verification, and doesn't expose an option to disable it.

Credentials are read from explicit arguments, ``JMAP_URL``/``JMAP_USERNAME``/``JMAP_PASSWORD`` environment variables, or a YAML config file (default ``~/.config/calendaring-jmap/calendar.yaml``). The config file is stored and read as plaintext; set restrictive file permissions on it (for example, ``chmod 600``) if you use it.

Untrusted calendar data
-----------------------

Servers can return iCalendar data that isn't fully spec-compliant. calendaring-jmap's fixup step (``convert/_fixup.py``) corrects a small, fixed set of known issues (missing ``DTSTAMP``, out-of-range dates, duplicate lines) using linear-time regular expressions with no nested quantifiers, so it isn't vulnerable to catastrophic backtracking on adversarial input.

JMAP servers, not this client, expand recurring events server-side. calendaring-jmap doesn't do client-side recurrence expansion, and doesn't read a search's time range or result count before sending it to the server.

Calendar data can contain personal information (names, locations, meeting content). If you accept calendar content from untrusted parties, treat it as sensitive data.

.. _dependencies:

Dependencies
============

calendaring-jmap depends on `niquests <https://pypi.org/project/niquests/>`_ or `requests <https://pypi.org/project/requests/>`_ for HTTP, `icalendar <https://pypi.org/project/icalendar/>`_ for iCalendar parsing, and `PyYAML <https://pypi.org/project/PyYAML/>`_ for config file loading (via ``yaml.safe_load``, which doesn't execute arbitrary code). See ``pyproject.toml`` for the current version constraints.
