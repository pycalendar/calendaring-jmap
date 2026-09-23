.. SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
.. SPDX-License-Identifier: AGPL-3.0-or-later

==========
Contribute
==========

This guide describes how to contribute to calendaring-jmap.

calendaring-jmap follows the Python Calendaring Ecosystem's `Code of Conduct <https://pycal.org/code-of-conduct/>`_.

Development setup
=================

.. code-block:: bash

    git clone https://github.com/pycalendar/calendaring-jmap
    cd calendaring-jmap
    pip install -e ".[dev]"
    pre-commit install

Running tests
=============

Unit tests, no server required:

.. code-block:: bash

    pytest src/calendaring_jmap/tests/test_jmap_unit.py

Integration tests, against Cyrus IMAP and Stalwart via Docker:

.. code-block:: bash

    tests/docker/cyrus/start.sh
    tests/docker/stalwart/start.sh
    pytest src/calendaring_jmap/tests/test_jmap_integration.py

Previewing docs
===============

.. code-block:: bash

    pip install -e ".[docs]"
    sphinx-autobuild docs docs/_build/html

Opens a local server that rebuilds and reloads the browser tab automatically as you edit files under ``docs/``.

.. _artificial-intelligence-policy:

Artificial intelligence policy
==============================

calendaring-jmap follows the Python Calendaring Ecosystem's `AI policy <https://pycal.org/ai-policy/>`_. Read it before using AI to help draft a pull request.

.. _commits-and-prs:

Commits and pull requests
==========================

Commit messages follow Conventional Commits: ``type: subject``, lowercase type, imperative mood, no period, e.g. ``fix: reject negative event duration``. Common types in this repo: ``feat``, ``fix``, ``test``, ``docs``, ``chore``, ``refactor``. A scope in parens is fine when it adds real information (``fix(convert): ...``), skip it otherwise. One line is enough; this project doesn't use commit bodies or trailers.

Keep each commit to one concern. A PR that both fixes a bug and refactors an unrelated helper should be two commits, not one with an unrelated diff mixed in; split it into two PRs instead if the two concerns don't obviously belong together (a broad cleanup PR touching many small, related things is fine as one PR with several commits inside it).

Write the PR description in your own words, as a short account of what you found and did, not a restatement of the linked issue's own text back at its author, and not a bullet-by-bullet checklist. If the work turned up real bugs along the way, describe the fix; don't frame the PR as "here's how many bugs I found," even when true, that framing reads as padding a body's length rather than reporting the work.

Before opening a PR: run the full local test suite (unit and, if your change touches client/server interaction, integration against Cyrus and/or Stalwart), ``ruff check`` and ``ruff format --check``, ``mypy``, and a docs build if you touched any ``:rfc:`` role or ``automodule``/``autoclass`` directive. Fix everything that comes back before asking for review, rather than leaving a known-red check for the reviewer to raise.

.. _change-log:

Change log
==========

If your PR changes behavior, add a news fragment. CI-only and internal-refactor PRs don't need one.

.. code-block:: bash

    touch news/<issue-number>.<type>.rst

Where ``<type>`` is one of: ``breaking``, ``removal``, ``feature``, ``bugfix``, ``documentation``, ``deps``, ``internal``, ``chore``, ``security``.

Write a short, user-facing description of the change inside the file: state what changed and, if it's a fix, what happened before. The issue link is generated automatically from the filename's number, so don't add one yourself. If your PR isn't tied to an issue, prefix the filename with ``+`` instead of a number (e.g. ``+conversion-validation.bugfix.rst``); towncrier accepts orphan fragments this way and just omits the issue link. Fragments are collected into ``CHANGES.rst`` at release time. Don't edit that file directly.

If you used AI to help write the change, briefly disclose it in the fragment, per the :ref:`artificial-intelligence-policy`.

To preview what the change log will look like:

.. code-block:: bash

    towncrier build --draft --version 0.0.0

If you're unsure whether your PR needs a fragment, ask a maintainer for the ``skip-changelog`` label rather than skipping silently.

.. _code-style:

Code style
==========

``ruff`` and ``mypy`` catch syntax and typing issues; they don't catch everything below, so review your own diff for it before opening a PR.

No duplication
    If you're about to write a function, block, or test fixture that's the same shape as one that already exists elsewhere (even with different variable names), extract a shared helper instead. This applies to tests as much as source: see ``_MockedClientMixin`` and the module-level ``_set_response``/``_get_response`` helpers in ``test_jmap_unit.py`` for the established pattern.

No hardcoding
    A literal that means something (a JMAP capability URN, an error type string, a format string) belongs in ``constants.py``, not inlined at every call site. See ``PARTICIPATION_STATUS_*``, ``UTC_DATETIME_FORMAT``, and ``LOCAL_DATETIME_FORMAT`` for the existing convention. A one-off literal with no reused meaning (a test fixture's arbitrary ID) doesn't need this.

RFC citations
    In docstrings (not comments, not test files, see below), cite a real, finalized RFC with Sphinx's ``:rfc:`` role, and always merge the section number into the role itself: ``:rfc:`8984#section-5.1.2``, not ``:rfc:`8984` section 5.1.2``. The latter renders as two disconnected pieces, a link to the RFC's front page and plain text next to it, so a reader has to manually find the section after clicking through. Verify the section number against the actual RFC text before citing it; don't guess from a related section or trust an existing comment's number without checking.

    A spec that has no RFC number yet (``draft-ietf-jmap-calendars`` as of this writing) can't use ``:rfc:``, since the role links to ``rfc-editor.org/rfc/rfcNNNN`` and there's no such page yet. Cite it as plain text (``draft-ietf-jmap-calendars section 2.2``) until it's assigned a number, then convert it.

    ``:rfc:`` only renders correctly in docstrings that Sphinx's ``automodule``/autodoc actually pulls in (check ``docs/reference/*.rst`` for which modules that is; private, underscore-prefixed modules aren't autodoc'd, so a role there is only for source readers, not the built docs). Plain ``#``-comments are never RST-parsed, and neither are docstrings inside ``tests/``, so use plain "RFC NNNN section N.N" text in both, not the role.

Prose in docstrings and comments
    No em dashes and no spaced hyphens (``word - word``) as a substitute for a colon, comma, or period; pick the punctuation the sentence actually needs. This reads as a tell for AI-generated text that wasn't edited, and the project wants human-edited prose throughout, disclosed AI use notwithstanding (see the :ref:`artificial-intelligence-policy`). Same for the Unicode arrow ``→`` used as shorthand for "produces" or "maps to": write out "to"/"produces"/"maps to", or use the ASCII ``->`` inside a code example where alignment matters.

Dead code
    Before adding a new helper, check whether an existing one already does the job. If your change makes a function unreachable, remove it in the same PR rather than leaving it for a future cleanup pass; check the milestone tracker first in case a near-term feature already claims it.

License
=======

By contributing, you agree your contributions are licensed under AGPL-3.0-or-later, same as the rest of the project.
