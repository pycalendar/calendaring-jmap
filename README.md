<!--- SPDX-FileCopyrightText: 2026 calendaring-jmap contributors -->
<!--- SPDX-License-Identifier: AGPL-3.0-or-later -->

# calendaring-jmap

Calendar operations over JMAP (RFC 8620 + draft-ietf-jmap-calendars), mirroring the public API of [python-caldav](https://github.com/python-caldav/caldav) so user code works against either protocol unmodified.

[![Tests](https://github.com/pycalendar/calendaring-jmap/actions/workflows/tests.yml/badge.svg)](https://github.com/pycalendar/calendaring-jmap/actions/workflows/tests.yml)
[![REUSE status](https://api.reuse.software/badge/github.com/pycalendar/calendaring-jmap)](https://api.reuse.software/info/github.com/pycalendar/calendaring-jmap)
[![License: AGPL-3.0-or-later](https://img.shields.io/badge/License-AGPL%203.0--or--later-blue.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

## Installation

```bash
pip install calendaring-jmap
```

Not yet published to PyPI — this package is mid-extraction from python-caldav (see [#9](https://github.com/pycalendar/calendaring-jmap/issues/9)).

## Usage

The library is being extracted from python-caldav's `caldav/jmap/` module ([#9](https://github.com/pycalendar/calendaring-jmap/issues/9)) and doesn't have working code here yet. Once that lands, this section will cover listing calendars, creating events, and incremental sync.

In the meantime, see python-caldav's existing JMAP docs: https://caldav.readthedocs.io/v3.0.0/jmap.html

## Documentation

Full documentation: https://calendaring-jmap.readthedocs.io/ (coming with [#8](https://github.com/pycalendar/calendaring-jmap/issues/8))

## Specs

- [RFC 8620](https://www.rfc-editor.org/rfc/rfc8620) — JMAP core
- [draft-ietf-jmap-calendars](https://datatracker.ietf.org/doc/draft-ietf-jmap-calendars/) — JMAP Calendars
- [RFC 8984](https://www.rfc-editor.org/rfc/rfc8984) — JSCalendar

## Related

- [python-caldav](https://github.com/python-caldav/caldav) — CalDAV client, source of this package's JMAP module
- [icalendar](https://github.com/collective/icalendar) — iCalendar parsing, used by both

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).
