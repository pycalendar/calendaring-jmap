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

calendaring-jmap only corrects ``apiUrl`` when it names the same host as the session endpoint but a different port or scheme (some servers do this). A session response naming a genuinely different host is followed as-is. Combined with TLS, this is safe against a passive network observer. It doesn't protect against a session endpoint that's itself malicious or already compromised, since that's the entity the protocol asks you to trust. Only point the client at a session URL you control or trust, and use HTTPS for it.

TLS and credentials
-------------------

calendaring-jmap does no custom TLS handling: it relies entirely on its HTTP library (niquests, or requests as a fallback, see :ref:`dependencies`) for certificate verification, and doesn't expose an option to disable it.

Credentials are read from explicit arguments, ``JMAP_URL``/``JMAP_USERNAME``/``JMAP_PASSWORD`` environment variables, or a YAML config file (default ``~/.config/calendaring-jmap/calendar.yaml``). The config file is stored and read as plaintext; set restrictive file permissions on it (for example, ``chmod 600``) if you use it.

Push subscription callback URL
------------------------------

:meth:`~calendaring_jmap.client.JMAPClient.subscribe_push`'s ``callback_url`` receives an unauthenticated HTTP POST from the server, carrying a verification code. :rfc:`8620#section-7.2.2` requires this code to have enough entropy that it can't be guessed, and requires you to submit it back before the server sends any further notifications there; this exists specifically so the server can confirm you control that URL before trusting it with real data, not so a server operator or a third party watching the endpoint can intercept anything useful on its own. Use an endpoint only you control, over HTTPS, the same guidance as the session URL above.

Untrusted calendar data
-----------------------

Servers can return iCalendar data that isn't fully spec-compliant. calendaring-jmap's fixup step (``convert/_fixup.py``) corrects a small, fixed set of known issues (missing ``DTSTAMP``, out-of-range dates, duplicate lines) using linear-time regular expressions with no nested quantifiers, so it isn't vulnerable to catastrophic backtracking on adversarial input.

JMAP servers, not this client, expand recurring events server-side. calendaring-jmap doesn't do client-side recurrence expansion, and doesn't read a search's time range or result count before sending it to the server.

Calendar data can contain personal information (names, locations, meeting content). If you accept calendar content from untrusted parties, treat it as sensitive data.

.. _dependencies:

Dependencies
============

calendaring-jmap depends on `niquests <https://pypi.org/project/niquests/>`_ for HTTP, `icalendar <https://pypi.org/project/icalendar/>`_ for iCalendar parsing, and `PyYAML <https://pypi.org/project/PyYAML/>`_ for config file loading (via ``yaml.safe_load``, which doesn't execute arbitrary code). `requests <https://pypi.org/project/requests/>`_ is an optional fallback (the ``requests`` extra) for environments where niquests isn't available. See ``pyproject.toml`` for the current version constraints.
