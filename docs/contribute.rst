.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

===========
Contribute
===========

This guide describes how to contribute to calendaring-jmap.

calendaring-jmap follows the Python Calendaring Ecosystem's `Code of Conduct <https://pycal.org/code-of-conduct/>`_.

Development setup
==================

.. code-block:: bash

    git clone https://github.com/pycalendar/calendaring-jmap
    cd calendaring-jmap
    pip install -e ".[dev]"
    pre-commit install

Running tests
==============

Unit tests, no server required:

.. code-block:: bash

    pytest src/calendaring_jmap/tests/test_jmap_unit.py

Integration tests, against Cyrus IMAP and Stalwart via Docker:

.. code-block:: bash

    tests/docker/cyrus/start.sh
    tests/docker/stalwart/start.sh
    pytest src/calendaring_jmap/tests/test_jmap_integration.py

.. _artificial-intelligence-policy:

Artificial intelligence policy
================================

calendaring-jmap follows the Python Calendaring Ecosystem's `AI policy <https://pycal.org/ai-policy/>`_. Read it before using AI to help draft a pull request.

.. _change-log:

Change log
===========

If your PR changes behavior, add a news fragment. CI-only and internal-refactor PRs don't need one.

.. code-block:: bash

    touch news/<issue-number>.<type>.rst

Where ``<type>`` is one of: ``breaking``, ``removal``, ``feature``, ``bugfix``, ``documentation``, ``deps``, ``internal``, ``chore``, ``security``.

Write a short, user-facing description of the change inside the file. Fragments are collected into ``CHANGES.rst`` at release time. Don't edit that file directly.

If you used AI to help write the change, briefly disclose it in the fragment, per the :ref:`artificial-intelligence-policy`.

To preview what the change log will look like:

.. code-block:: bash

    towncrier build --draft --version 0.0.0

If you're unsure whether your PR needs a fragment, ask a maintainer for the ``skip-changelog`` label rather than skipping silently.

License
========

By contributing, you agree your contributions are licensed under AGPL-3.0-or-later, same as the rest of the project.
