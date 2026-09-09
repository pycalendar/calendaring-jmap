<!--- SPDX-FileCopyrightText: 2026 calendaring-jmap contributors -->
<!--- SPDX-License-Identifier: AGPL-3.0-or-later -->

# Contributing to calendaring-jmap

## Development setup

```bash
git clone https://github.com/pycalendar/calendaring-jmap
cd calendaring-jmap
pip install -e ".[dev]"
pre-commit install
```

## Running tests

Unit tests (no server required):

```bash
pytest src/calendaring_jmap/tests/test_jmap_unit.py
```

Integration tests, against Cyrus IMAP via Docker:

```bash
docker-compose -f tests/docker/cyrus/docker-compose.yml up -d
pytest src/calendaring_jmap/tests/test_jmap_integration.py
```

## Change log

If your PR changes behavior, add a news fragment. CI-only and internal-refactor PRs don't need one.

```bash
touch news/<issue-number>.<type>.rst
```

Where `<type>` is one of: `breaking`, `removal`, `feature`, `bugfix`, `documentation`, `deps`, `internal`, `chore`, `security`.

Write a short, user-facing description of the change inside the file. Fragments are collected into [CHANGES.rst](CHANGES.rst) at release time. Don't edit that file directly.

To preview what the change log will look like:

```bash
towncrier build --draft --version 0.0.0
```

If your PR genuinely doesn't need a fragment (CI, docs-only, internal refactor), ask a maintainer to add the `skip-changelog` label instead of skipping this silently.

## License

By contributing, you agree your contributions are licensed under AGPL-3.0-or-later, same as the rest of the project. See [LICENSE](LICENSE).
