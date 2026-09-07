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
pytest tests/test_jmap_unit.py
```

Integration tests, against Cyrus IMAP via Docker:

```bash
docker-compose -f tests/docker/cyrus/docker-compose.yml up -d
pytest tests/test_jmap_integration.py
```

## License

By contributing, you agree your contributions are licensed under AGPL-3.0-or-later, same as the rest of the project. See [LICENSE](LICENSE).
