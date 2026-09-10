<!--- SPDX-FileCopyrightText: 2026 calendaring-jmap contributors -->
<!--- SPDX-License-Identifier: AGPL-3.0-or-later -->

# calendaring-jmap

Calendar operations over JMAP (RFC 8620 + draft-ietf-jmap-calendars).

[![Documentation](https://readthedocs.org/projects/calendaring-jmap/badge/?version=stable)](https://calendaring-jmap.readthedocs.io/en/stable/)
[![Tests](https://github.com/pycalendar/calendaring-jmap/actions/workflows/tests.yml/badge.svg)](https://github.com/pycalendar/calendaring-jmap/actions/workflows/tests.yml)
[![codecov](https://codecov.io/gh/pycalendar/calendaring-jmap/branch/main/graph/badge.svg)](https://codecov.io/gh/pycalendar/calendaring-jmap)
[![REUSE status](https://api.reuse.software/badge/github.com/pycalendar/calendaring-jmap)](https://api.reuse.software/info/github.com/pycalendar/calendaring-jmap)
[![License: AGPL-3.0-or-later](https://img.shields.io/badge/License-AGPL%203.0--or--later-blue.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

## Installation

```bash
pip install calendaring-jmap
```

Not yet published to PyPI.

## Usage

```python
from calendaring_jmap import get_jmap_client

with get_jmap_client(
    url="https://jmap.example.com/.well-known/jmap",
    username="alice",
    password="secret",
) as client:
    calendars = client.get_calendars()
    for cal in calendars:
        print(cal.name)
```

See the [quickstart](https://calendaring-jmap.readthedocs.io/en/latest/usage/quickstart.html) for authentication options, error handling, and configuration from environment variables or a YAML file.

## Documentation

Full documentation: https://calendaring-jmap.readthedocs.io/

## Specs

- [RFC 8620](https://www.rfc-editor.org/rfc/rfc8620): JMAP core
- [draft-ietf-jmap-calendars](https://datatracker.ietf.org/doc/draft-ietf-jmap-calendars/): JMAP Calendars
- [RFC 8984](https://www.rfc-editor.org/rfc/rfc8984): JSCalendar

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).
