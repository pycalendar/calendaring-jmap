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

Add a test to ``test_jmap_unit.py`` for anything that doesn't need a real server: request-building, response parsing, error handling, conversion logic. Mock HTTP with ``unittest.mock``, following ``_MockedClientMixin``'s pattern. Add a test to ``test_jmap_integration.py`` only for behavior that depends on how a real server actually responds, such as a server-specific quirk or a capability check; each test class there skips automatically when its server isn't reachable, so a missing Docker container doesn't fail CI, it just skips those tests.

Previewing docs
===============

.. code-block:: bash

    pip install -e ".[docs]"
    sphinx-autobuild docs docs/_build/html

Opens a local server that rebuilds and reloads the browser tab automatically as you edit files under ``docs/``.

.. _vale-check:

Checking docs prose with Vale
=============================

.. code-block:: bash

    vale sync
    vale docs/

CI runs this the same way, as a warn-only check (a failure doesn't block a PR yet): it catches a real subset of :ref:`writing-documentation`'s rules mechanically, but it isn't a substitute for reading your own diff.

.. _artificial-intelligence-policy:

Artificial intelligence policy
==============================

calendaring-jmap follows the Python Calendaring Ecosystem's `AI policy <https://pycal.org/ai-policy/>`_. Read it before using AI to help draft a pull request.

.. _commits-and-prs:

Commits and pull requests
=========================

Commit messages follow Conventional Commits: ``type: subject``, lowercase type, imperative mood, no period, for example ``fix: reject negative event duration``. Common types in this repo: ``feat``, ``fix``, ``test``, ``docs``, ``chore``, ``refactor``. A scope in parens is fine when it adds real information (``fix(convert): ...``), skip it otherwise. One line is enough; this project doesn't use commit bodies or trailers.

Keep each commit to one concern. A PR that both fixes a bug and refactors an unrelated helper should be two commits, not one with an unrelated diff mixed in; split it into two PRs instead if the two concerns don't obviously belong together (a broad cleanup PR touching many small, related things is fine as one PR with several commits inside it).

Write the PR description in your own words, as a short account of what you found and did, not a restatement of the linked issue's own text back at its author, and not a bullet-by-bullet checklist. If the work turned up real bugs along the way, describe the fix; don't frame the PR as "here's how many bugs I found," even when true, that framing reads as padding a body's length rather than reporting the work.

Before opening a PR: run the full local test suite (unit and, if your change touches client/server interaction, integration against Cyrus and/or Stalwart), ``ruff check`` and ``ruff format --check``, ``mypy``, and :ref:`vale-check` if you touched any ``.rst`` page. Fix everything that comes back before asking for review, rather than leaving a known-red check for the reviewer to raise. Vale runs warn-only in CI for now, so it won't block the PR on its own, but treat its findings the same as the others. CI also builds the docs with Sphinx's warnings-as-errors flag, so a broken ``:rfc:`` role, cross-reference, or ``automodule``/``autoclass`` directive fails the PR there rather than silently deploying a broken page to Read the Docs.

.. _change-log:

Change log
==========

If your PR changes behavior, add a news fragment. CI-only and internal-refactor PRs don't need one.

.. code-block:: bash

    touch news/<issue-number>.<type>.rst

Where ``<type>`` is one of: ``breaking``, ``removal``, ``feature``, ``bugfix``, ``documentation``, ``deps``, ``internal``, ``chore``, ``security``.

Write a short, user-facing description of the change inside the file: state what changed and, if it's a fix, what happened before. The issue link is generated automatically from the filename's number, so don't add one yourself. If your PR isn't tied to an issue, prefix the filename with ``+`` instead of a number (for example ``+conversion-validation.bugfix.rst``); towncrier accepts orphan fragments this way and just omits the issue link. Fragments are collected into ``docs/changelog.rst`` at release time. Don't edit that file directly. See :doc:`release` for the maintainer-only steps that actually cut a release.

Keep each fragment to one or two sentences. State the change; don't explain how it works internally, why it was needed, or how it was found, that belongs in the PR description, not the changelog. If your PR touches more than one distinct behavior (two separate bugs, or a feature plus an unrelated parameter addition), write one fragment per behavior (``11.feature.1.rst``, ``11.feature.2.rst``, and further numbered fragments as needed) instead of one fragment covering all of them. See any ``bugfix`` fragment in ``news/`` for the length and tone to aim for.

If you used AI to help write the change, briefly disclose it in the fragment, per the :ref:`artificial-intelligence-policy`.

To preview what the change log will look like:

.. code-block:: bash

    towncrier build --draft --version 0.0.0

If you're unsure whether your PR needs a fragment, ask a maintainer for the ``skip-changelog`` label rather than skipping silently.

.. _code-style:

Code style
==========

``ruff`` and ``mypy`` catch syntax and typing issues; they don't catch everything below, so review your own diff for it before opening a PR.

Sync/async parity
    A new or changed public method on :class:`~calendaring_jmap.client.JMAPClient` needs the matching method on :class:`~calendaring_jmap.async_client.AsyncJMAPClient`, with the same signature and behavior, differing only in that it's a coroutine. See :doc:`explanation/design` for why this parity exists and how the shared base class keeps the two clients from duplicating their parsing and request-building logic.

No duplication
    If you're about to write a function, block, or test fixture that's the same shape as one that already exists elsewhere (even with different variable names), extract a shared helper instead. This applies to tests as much as source: see ``_MockedClientMixin`` and the module-level ``_set_response``/``_get_response`` helpers in ``test_jmap_unit.py`` for the established pattern.

No hardcoding
    A literal that means something (a JMAP capability URN, an error type string, a format string) belongs in ``constants.py``, not inlined at every call site. See ``PARTICIPATION_STATUS_*``, ``UTC_DATETIME_FORMAT``, and ``LOCAL_DATETIME_FORMAT`` for the existing convention. A one-off literal with no reused meaning (a test fixture's arbitrary ID) doesn't need this.

RFC citations
    In docstrings (not comments, not test files, see below), cite a real, finalized RFC with Sphinx's ``:rfc:`` role, and always merge the section number into the role itself: ``:rfc:`8984#section-5.1.2``, not ``:rfc:`8984` section 5.1.2``. The latter renders as two disconnected pieces, a link to the RFC's front page and plain text next to it, so a reader has to manually find the section after clicking through. Verify the section number against the actual RFC text before citing it; don't guess from a related section or trust an existing comment's number without checking.

    A spec that has no RFC number yet (``draft-ietf-jmap-calendars`` as of this writing) can't use ``:rfc:``, since the role links to ``datatracker.ietf.org/doc/html/rfcNNNN.html`` and there's no such page yet. Cite it as plain text (``draft-ietf-jmap-calendars section 2.2``) until it's assigned a number, then convert it.

    ``:rfc:`` only renders correctly in docstrings that Sphinx's ``automodule``/autodoc actually pulls in (check ``docs/reference/*.rst`` for which modules that is; private, underscore-prefixed modules aren't autodoc'd, so a role there is only for source readers, not the built docs). Plain ``#``-comments are never RST-parsed, and neither are docstrings inside ``tests/``, so use plain "RFC NNNN section N.N" text in both, not the role.

.. _prose-docstrings-comments:

Prose in docstrings and comments
    Write plainly and specifically: say what's actually true about this code, not a general description that could apply to any project. Pick the punctuation a sentence actually needs instead of leaning on an em dash or a spaced hyphen (``word - word``) as a stand-in for a colon, comma, or period; the same goes for the Unicode arrow ``→`` used as shorthand for "produces" or "maps to," which reads clearer written out, or as ASCII ``->`` inside a code example where alignment matters.

    A few words read as filler more often than not and are usually worth cutting: delve, leverage, utilize (say "use"), robust, seamless, pivotal, cutting-edge, game-changer. If a sentence is true and specific without one of these, it almost never needs it back in.

Dead code
    Before adding a new helper, check whether an existing one already does the job. If your change makes a function unreachable, remove it in the same PR rather than leaving it for a future cleanup pass; check the milestone tracker first in case a near-term feature already claims it.

.. _writing-documentation:

Writing documentation
=====================

This section covers ``docs/*.rst`` page prose: how-to guides, tutorials, reference pages, and explanation pages. For docstring and comment conventions, see :ref:`code-style` above; this section cross-references those rules rather than restating them.

Choosing a Diataxis category
    calendaring-jmap's docs are organized into four categories: tutorials, how-to guides, reference, and explanation. This split comes from the `Diátaxis framework <https://diataxis.fr/>`_. Django's docs use the same four-way split, under different section names; pytest's docs use nearly these same names. Each category answers a different question, so pick the one matching what the reader actually needs:

    - **Tutorial** (``tutorials/``): a single guided path a newcomer follows start to finish, learning by doing. See :doc:`tutorials/quickstart` for the shape this takes. New content almost never belongs here: a tutorial teaches someone who knows nothing yet, which is a narrow, rarely-needed case, and most new content is really a how-to.
    - **How-to guide** (``how-to/``): a terse recipe for someone who already knows the basics and wants to do one specific thing. No "why" content inline: cross-reference :doc:`explanation/design` instead (see ``how-to/errors.rst``'s opening line for the pattern).
    - **Reference** (``reference/``): generated, factual lookup material. This is almost entirely autodoc output; hand-written prose here should be limited to a short page intro, not an explanation of design choices.
    - **Explanation** (``explanation/``): the why, background, and design rationale, kept separate so how-to pages stay scannable. ``explanation/design.rst``'s existing "Why X" section headings are the pattern to match.

Write about JMAP, not about other protocols
    A reader of these docs is here to learn JMAP, not CalDAV, WebDAV, or anything else. Don't explain a JMAP behavior by contrasting it against how another protocol or library does the same thing, even as a single clarifying aside: state the JMAP fact on its own terms. This applies to docstrings too, not just ``.rst`` pages, since they render into the published reference.

Prose style for pages
    Page prose follows the `Microsoft Writing Style Guide <https://learn.microsoft.com/style-guide/welcome/>`_: plain language, active voice, second person ("you," not "the user"), sentence-case headings, no exclamation points. Contractions are fine. Avoid Latin abbreviations in prose ("for example," not "e.g."; "that is," not "i.e."). This is a different, more conversational register than :ref:`prose-docstrings-comments`'s rule, which governs terse, factual docstring/comment text; that rule's bans apply here too, unchanged.

    Write each paragraph as one line, not hard-wrapped at a fixed column width. RST collapses whitespace, so wrapping serves no rendering purpose; every existing page in this repo is written this way.

    Avoid "simple," "just," "very," and "easy": a reader who can't complete the task as described finds these words condescending rather than reassuring. Prefer "such as" over "like" when introducing an example, "select" over "check"/"tick" for a UI action, and "verify" over "be sure" when asking the reader to confirm something.

    Spell these terms consistently: GitHub, add-on (not addon), plug-in (not plugin), reST or reStructuredText (not RST, except as the file extension or in a code span like ``.rst``).

    :ref:`vale-check` runs the Microsoft Vale style package plus `signs-of-ai-writing <https://github.com/ammil-industries/vale-signs-of-ai-writing>`_ (patterns from Wikipedia's `Signs of AI writing <https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing>`_ essay) in CI, catching a real subset of these rules mechanically: contractions, passive voice, dash spacing, sentence-case headings, hedging clusters, chatbot phrases, and the AI-typical vocabulary and symbolic-language patterns the essay documents. It doesn't catch everything: the Diataxis category choice and whether a how-to page pushed "why" content where it belongs still need a reviewer's judgment.

Cross-referencing and code examples
    Link between pages with ``:doc:`` and an explicit relative path (``:doc:`../explanation/design```). Reference a class or method with ``:class:``/``:meth:`` and a leading ``~`` for a short display name (``:class:`~calendaring_jmap.objects.calendar.JMAPCalendar```). Cite an RFC the same way :ref:`code-style`'s "RFC citations" rule describes for docstrings, merging the section into the role.

    Use ``.. code-block:: python`` or ``.. code-block:: bash``, with a blank line before and after the directive and the indented block. A tutorial ends with an explicit "Next steps" section linking forward to relevant how-to/explanation pages, matching ``tutorials/quickstart.rst``.

Title underlines
    RST title underlines must match the title's exact character width. Sphinx only warns about this for a page title's overline/underline pair, not for an ordinary section heading's underline alone, so a mismatched section underline builds silently; count it yourself rather than assume a clean build means it's correct.

License
=======

By contributing, you agree your contributions are licensed under AGPL-3.0-or-later, same as the rest of the project.
